"""Unix-socket server for the controller service.

A role-aware, single-threaded ~50 Hz loop wrapping a ControllerService: it
accepts client connections, runs the hello handshake, forwards writer commands
to the service, and broadcasts the service's state and events to every client.
Preview frames are an opt-in stream sent only to the single frame subscriber
(see subscribe_frames). The service holds the device-facing half; this shell
holds only the client sockets.
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
from dataclasses import dataclass

from .config import (
    DEFAULT_CONFIG_PATH, DEFAULT_LOGS_PATH, DEFAULT_SOCKET_PATH, ConfigError,
    load_config, resolve_config_path, resolve_runtime_path,
)
from .controller_protocol import (
    KIND_JSON,
    PROTOCOL_VERSION,
    ROLE_OBSERVER,
    ROLE_WRITER,
    ProtocolReader,
    encode_json,
    parse_json_payload,
)
from .service import ControllerService
from .slogger import configure_logger
from .version import get_runtime_version

log = logging.getLogger(__name__)

_TICK_INTERVAL_S = 0.020   # ~50 Hz


@dataclass
class _ClientConn:
    sock: socket.socket
    reader: ProtocolReader
    desc: str | None
    role: str | None = None
    hello_ok: bool = False


def _describe_peer(conn: socket.socket) -> str | None:
    """Best-effort human label for a connected client, from its pid."""
    if not hasattr(socket, 'SO_PEERCRED'):
        return None
    try:
        raw = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED,
                              struct.calcsize('3i'))
        pid, _uid, _gid = struct.unpack('3i', raw)
    except (AttributeError, OSError, struct.error):
        return None
    return f'pid={pid}'


class ControllerServer:
    """Role-aware multi-client unix-socket server wrapping a ControllerService."""

    def __init__(self, service: ControllerService, socket_path: str):
        self._service = service
        self._socket_path = socket_path
        self._running = False
        self._server_sock: socket.socket | None = None
        self._clients: dict[int, _ClientConn] = {}
        self._frame_subscriber: _ClientConn | None = None   # last subscriber wins

    def run(self) -> None:
        """Blocking main loop; returns on shutdown."""
        try:
            os.unlink(self._socket_path)
        except FileNotFoundError:
            pass

        self._server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server_sock.bind(self._socket_path)
        self._server_sock.listen(16)
        self._server_sock.setblocking(False)
        self._running = True
        log.info('listening on %s', self._socket_path)

        try:
            while self._running and not self._service.should_shutdown:
                self._accept_clients()
                self._read_clients()
                json_msgs, frame_msgs = self._service.tick_once()
                self._broadcast(json_msgs, reliable=True)
                self._send_frames(frame_msgs)
                time.sleep(_TICK_INTERVAL_S)
        finally:
            for client in list(self._clients.values()):
                self._close_client(client)
            if self._server_sock is not None:
                self._server_sock.close()
                self._server_sock = None
            try:
                os.unlink(self._socket_path)
            except FileNotFoundError:
                pass
            self._service.close()

    def shutdown(self) -> None:
        self._running = False

    # ------------------------------------------------------------------

    def _accept_clients(self) -> None:
        if self._server_sock is None:
            return
        while True:
            try:
                conn, _ = self._server_sock.accept()
            except (BlockingIOError, OSError):
                return
            conn.setblocking(False)
            client = _ClientConn(sock=conn, reader=ProtocolReader(),
                                 desc=_describe_peer(conn))
            self._clients[conn.fileno()] = client
            log.info('client connected%s',
                     f': {client.desc}' if client.desc else '')

    def _read_clients(self) -> None:
        if not self._clients:
            return
        sockets = [c.sock for c in self._clients.values()]
        try:
            readable, _, _ = select.select(sockets, [], [], 0)
        except OSError:
            for client in list(self._clients.values()):
                self._close_client(client)
            return

        for sock in readable:
            client = self._clients.get(sock.fileno())
            if client is None:
                continue
            try:
                data = sock.recv(4096)
            except BlockingIOError:
                continue
            except OSError:
                self._close_client(client)
                continue
            if not data:
                self._close_client(client)
                continue
            self._dispatch_client(client, data)

    def _dispatch_client(self, client: _ClientConn, data: bytes) -> None:
        client.reader.feed(data)
        for kind, payload in client.reader.messages():
            if not client.hello_ok:
                if self._handle_hello(client, kind, payload):
                    self._close_client(client)
                    return
                continue
            if kind != KIND_JSON:
                continue
            try:
                cmd = parse_json_payload(payload)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if cmd.get('cmd') == 'hello':
                self._send(client, self._reply(cmd.get('id'), False,
                                               error='hello already completed'))
                continue
            # Frame subscription is connection state, allowed from any role
            # (a control+display client is a writer that wants frames), so it
            # is handled here, before the writer-only gate.
            if cmd.get('cmd') in ('subscribe_frames', 'unsubscribe_frames'):
                self._handle_subscription(client, cmd.get('id'), cmd['cmd'])
                continue
            if client.role != ROLE_WRITER:
                self._send(client, self._reply(cmd.get('id'), False,
                           error='observer connections cannot send commands'))
                continue
            self._send(client, self._service.handle_cmd(cmd))

    def _handle_hello(self, client: _ClientConn, kind: int, payload: bytes) -> bool:
        """Process the first message. Returns True when the client must be
        dropped (a bad or missing hello)."""
        if kind != KIND_JSON:
            return True
        try:
            cmd = parse_json_payload(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return True
        cmd_id = cmd.get('id')
        if cmd.get('cmd') != 'hello':
            self._send(client, self._reply(cmd_id, False,
                                           error='first command must be hello'))
            return True
        if cmd.get('protocol_version') != PROTOCOL_VERSION:
            self._send(client, self._reply(cmd_id, False, error=(
                f'protocol_version mismatch: expected {PROTOCOL_VERSION}, '
                f'got {cmd.get("protocol_version")!r}')))
            return True
        role = cmd.get('role')
        if role not in {ROLE_WRITER, ROLE_OBSERVER}:
            self._send(client, self._reply(cmd_id, False,
                                           error='role must be "writer" or "observer"'))
            return True

        client.role = role
        client.hello_ok = True
        self._send(client, self._reply(cmd_id, True, result={'role': role}))
        for msg in self._service.snapshot_messages():
            self._send_bytes(client, msg, reliable=True)
        return False

    def _handle_subscription(self, client: _ClientConn, cmd_id, name: str) -> None:
        """Frame subscription, single subscriber, last register wins. Preview
        assembly is enabled only on the empty -> subscribed edge and disabled
        on the subscribed -> empty edge; replacing one subscriber with another
        just moves the target."""
        if name == 'subscribe_frames':
            if self._frame_subscriber is None:
                self._service.set_preview_enabled(True)
            self._frame_subscriber = client
        elif self._frame_subscriber is client:   # unsubscribe; ignore if stale
            self._frame_subscriber = None
            self._service.set_preview_enabled(False)
        self._send(client, self._reply(cmd_id, True))

    def _send_frames(self, frame_msgs: list[bytes]) -> None:
        for msg in frame_msgs:
            sub = self._frame_subscriber   # re-read: a send may close it
            if sub is None:
                return
            self._send_bytes(sub, msg, reliable=False)

    def _broadcast(self, messages: list[bytes], *, reliable: bool) -> None:
        for msg in messages:
            for client in list(self._clients.values()):
                if client.hello_ok:
                    self._send_bytes(client, msg, reliable=reliable)

    @staticmethod
    def _reply(cmd_id, ok, *, result=None, error=None) -> dict:
        msg = {'type': 'reply', 'id': cmd_id, 'ok': ok}
        if ok:
            msg['result'] = result or {}
        else:
            msg['error'] = error or 'error'
        return msg

    def _send(self, client: _ClientConn, msg: dict) -> None:
        self._send_bytes(client, encode_json(msg), reliable=True)

    def _send_bytes(self, client: _ClientConn, data: bytes, *, reliable: bool) -> bool:
        try:
            sent = client.sock.send(data)
        except BlockingIOError:
            if reliable:
                self._close_client(client)
            return False
        except OSError:
            self._close_client(client)
            return False
        if sent == len(data):
            return True
        # A partial write desynchronizes the stream; drop the client.
        self._close_client(client)
        return False

    def _close_client(self, client: _ClientConn) -> None:
        self._clients.pop(client.sock.fileno(), None)
        if self._frame_subscriber is client:
            self._frame_subscriber = None
            self._service.set_preview_enabled(False)
        try:
            client.sock.close()
        except OSError:
            pass
        log.info('client disconnected%s',
                 f' role={client.role}' if client.role else '')


def main() -> None:
    """CLI entry point for `elemctl server`."""
    parser = argparse.ArgumentParser(prog='elemctl server',
                                     description='Elements controller server')
    parser.add_argument('--config', default=None,
                        help=f'config JSON path (default: {DEFAULT_CONFIG_PATH})')
    parser.add_argument('--socket', default=DEFAULT_SOCKET_PATH,
                        help='unix socket path (default: %(default)s)')
    parser.add_argument('--log-dir', default=None,
                        help='logs directory (default: controller.logs_dir or <repo>/logs)')
    parser.add_argument('--debug', action='store_true', help='enable debug logging')
    args = parser.parse_args()

    log_level = 'DEBUG' if args.debug else 'INFO'
    configure_logger(level=log_level)
    try:
        config_path = resolve_config_path(args.config)
        config = load_config(config_path)
    except (ConfigError, json.JSONDecodeError) as e:
        log.error('config error: %s', e)
        sys.exit(1)

    log_dir = resolve_runtime_path(args.log_dir, config.logs_dir, DEFAULT_LOGS_PATH)
    configure_logger(logfile=os.path.join(log_dir, 'server.log'), level=log_level)
    log.info('elements controller started. version: %s', get_runtime_version())
    log.info('using config %s', config_path)

    service = ControllerService(config, config_path=config_path)
    server = ControllerServer(service, os.path.expanduser(args.socket))

    def _on_signal(signum, frame):
        server.shutdown()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    server.run()


if __name__ == '__main__':
    main()
