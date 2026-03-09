"""UDS server for the controller service.

Provides a Unix Domain Socket interface wrapping ControllerService.
Single-client, single-threaded, ~50Hz tick loop.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import select
import signal
import socket
import sys
import time

from .config import ConfigError, load_config
from .service import ControllerService
from .uds_wire import UdsReader, encode_json, parse_json_payload, KIND_JSON

log = logging.getLogger(__name__)

_TICK_INTERVAL = 0.020  # ~50Hz


class UdsServer:
    """Single-client UDS server wrapping a ControllerService."""

    def __init__(self, service: ControllerService, socket_path: str):
        self._service = service
        self._socket_path = socket_path
        self._running = False

        self._server_sock: socket.socket | None = None
        self._client_sock: socket.socket | None = None
        self._reader = UdsReader()

    def run(self) -> None:
        """Blocking main loop. Returns when shutdown or stopped."""
        # Remove stale socket
        try:
            os.unlink(self._socket_path)
        except FileNotFoundError:
            pass

        self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_sock.bind(self._socket_path)
        self._server_sock.listen(1)
        self._server_sock.setblocking(False)
        self._running = True

        log.info('listening on %s', self._socket_path)

        try:
            while self._running and not self._service.should_shutdown:
                self._accept_client()
                self._read_client()

                json_msgs, frame_msgs = self._service.tick_once()

                self._send_messages(json_msgs)
                self._send_messages(frame_msgs)

                time.sleep(_TICK_INTERVAL)
        finally:
            self._close_client()
            if self._server_sock is not None:
                self._server_sock.close()
                self._server_sock = None
            try:
                os.unlink(self._socket_path)
            except FileNotFoundError:
                pass
            self._service.close()

    def shutdown(self) -> None:
        """Signal the main loop to stop."""
        self._running = False

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _accept_client(self) -> None:
        if self._server_sock is None:
            return
        try:
            conn, _ = self._server_sock.accept()
        except BlockingIOError:
            return

        if self._client_sock is not None:
            # Single-client policy: reject new connection
            conn.close()
            return

        conn.settimeout(1.0)
        self._client_sock = conn
        self._reader = UdsReader()
        log.info('client connected')

        # Probe all devices, then send snapshot
        self._service.probe_all()
        snapshot = self._service.build_snapshot()
        self._send_one(encode_json(snapshot))

    def _read_client(self) -> None:
        if self._client_sock is None:
            return

        # Non-blocking: only read if data is available
        readable, _, _ = select.select([self._client_sock], [], [], 0)
        if not readable:
            return

        try:
            data = self._client_sock.recv(4096)
        except (BlockingIOError, socket.timeout):
            return
        except OSError:
            self._close_client()
            return

        if not data:
            self._close_client()
            return

        self._reader.feed(data)
        for kind, payload in self._reader.messages():
            if kind != KIND_JSON:
                continue
            try:
                cmd = parse_json_payload(payload)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            reply = self._service.handle_cmd(cmd)
            self._send_one(encode_json(reply))

    def _send_messages(self, messages: list[bytes]) -> None:
        for msg in messages:
            self._send_one(msg)

    def _send_one(self, data: bytes) -> None:
        if self._client_sock is None:
            return
        try:
            self._client_sock.sendall(data)
        except (BrokenPipeError, ConnectionResetError, socket.timeout, OSError):
            self._close_client()

    def _close_client(self) -> None:
        if self._client_sock is not None:
            try:
                self._client_sock.close()
            except OSError:
                pass
            self._client_sock = None
            log.info('client disconnected')


def main() -> None:
    """CLI entry point for python -m elemctl.serve."""
    parser = argparse.ArgumentParser(
        prog='elemctl.serve',
        description='Elements controller service (UDS)',
    )
    parser.add_argument('--config', required=True, help='config JSON path')
    parser.add_argument('--socket', required=True, help='UDS socket path')

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format='%(levelname)s %(name)s: %(message)s',
    )

    try:
        config = load_config(args.config)
    except (ConfigError, FileNotFoundError, json.JSONDecodeError) as e:
        log.error('config error: %s', e)
        sys.exit(1)

    service = ControllerService(config)
    server = UdsServer(service, args.socket)

    def _on_signal(signum, frame):
        server.shutdown()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    server.run()
