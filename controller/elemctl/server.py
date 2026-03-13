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
import struct
import sys
import time

from .config import (
    DEFAULT_CONFIG_PATH, DEFAULT_LOGS_PATH, DEFAULT_SOCKET_PATH, ConfigError,
    load_config, resolve_config_path, resolve_runtime_path,
)
from .service import ControllerService
from .slogger import configure_logger
from .uds_wire import UdsReader, encode_json, parse_json_payload, KIND_JSON
from .version import get_runtime_version

log = logging.getLogger(__name__)

_TICK_INTERVAL = 0.020  # ~50Hz
_SERVER_ALIASES = {'server', 'serve'}


def _peer_label(argv: list[str]) -> str | None:
    if not argv:
        return None

    exe = os.path.basename(argv[0])
    if exe == 'elemctl' and len(argv) > 1:
        sub = argv[1]
        if sub in {'tui', 'sim', 'run'}:
            return f'elemctl {sub}'
        if sub in _SERVER_ALIASES:
            return 'elemctl server'

    if '-m' in argv:
        try:
            mod = argv[argv.index('-m') + 1]
        except IndexError:
            mod = ''
        if mod == 'elemctl.tui':
            return 'elemctl tui'
        if mod in {'elemctl.server', 'elemctl.serve'}:
            return 'elemctl server'
        if mod == 'elemctl.sim':
            return 'elemctl sim'
        if mod == 'elemctl':
            for arg in argv[argv.index('-m') + 2:]:
                if arg in {'tui', 'sim', 'run'}:
                    return f'elemctl {arg}'
                if arg in _SERVER_ALIASES:
                    return 'elemctl server'

    if exe.startswith('python'):
        for arg in argv[1:]:
            if arg in {'tui', 'sim', 'run'}:
                return f'elemctl {arg}'
            if arg in {'server', 'serve'}:
                return 'elemctl server'
            if arg == 'elemctl.tui':
                return 'elemctl tui'
            if arg in {'elemctl.server', 'elemctl.serve'}:
                return 'elemctl server'
            if arg == 'elemctl.sim':
                return 'elemctl sim'

    return exe or None


def _describe_peer(conn: socket.socket) -> str | None:
    if not hasattr(socket, 'SO_PEERCRED'):
        return None

    try:
        raw = conn.getsockopt(
            socket.SOL_SOCKET,
            socket.SO_PEERCRED,
            struct.calcsize('3i'),
        )
        pid, _uid, _gid = struct.unpack('3i', raw)
    except (AttributeError, OSError, struct.error):
        return None

    label = None
    try:
        with open(f'/proc/{pid}/cmdline', 'rb') as f:
            argv = [
                arg.decode(errors='replace')
                for arg in f.read().split(b'\0')
                if arg
            ]
        label = _peer_label(argv)
    except OSError:
        pass

    if label:
        return f'{label} pid={pid}'
    return f'pid={pid}'


class UdsServer:
    """Single-client UDS server wrapping a ControllerService."""

    def __init__(self, service: ControllerService, socket_path: str):
        self._service = service
        self._socket_path = socket_path
        self._running = False

        self._server_sock: socket.socket | None = None
        self._client_sock: socket.socket | None = None
        self._client_desc: str | None = None
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
        self._client_desc = _describe_peer(conn)
        self._reader = UdsReader()
        if self._client_desc is not None:
            log.info('client connected: %s', self._client_desc)
        else:
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
            desc = self._client_desc
            self._client_desc = None
            if desc is not None:
                log.info('client disconnected: %s', desc)
            else:
                log.info('client disconnected')


def main() -> None:
    """CLI entry point for elemctl server."""
    parser = argparse.ArgumentParser(
        prog='elemctl server',
        description='Elements controller server (UDS)',
    )
    parser.add_argument(
        '--config', default=DEFAULT_CONFIG_PATH,
        help='config JSON path (default: %(default)s)',
    )
    parser.add_argument(
        '--socket', default=DEFAULT_SOCKET_PATH,
        help='UDS socket path (default: %(default)s)',
    )
    parser.add_argument(
        '--log-dir', default=None,
        help='logs directory (default: controller.logs_dir or <repo>/logs)',
    )

    args = parser.parse_args()
    configure_logger(level="INFO")

    try:
        config_path = resolve_config_path(args.config)
        config = load_config(config_path)
    except (ConfigError, json.JSONDecodeError) as e:
        log.error('config error: %s', e)
        sys.exit(1)

    log_dir = resolve_runtime_path(args.log_dir, config.logs_dir, DEFAULT_LOGS_PATH)
    configure_logger(
        logfile=os.path.join(log_dir, 'server.log'),
        level="INFO",
    )
    log.info('elements controller started. version: %s', get_runtime_version())

    service = ControllerService(config, config_path=config_path)
    socket_path = os.path.expanduser(args.socket)
    server = UdsServer(service, socket_path)

    def _on_signal(signum, frame):
        server.shutdown()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    server.run()


if __name__ == '__main__':
    main()
