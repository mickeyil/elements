"""Unix socket server for the controller service.

Provides a unix socket interface wrapping ControllerService.
Role-aware, single-threaded, ~50Hz tick loop.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging
import os
import select
import signal
import socket
import struct
import sys
import time

from elemctl.config import (
    DEFAULT_CONFIG_PATH, DEFAULT_LOGS_PATH, DEFAULT_SOCKET_PATH, ConfigError,
    load_config, resolve_config_path, resolve_runtime_path,
)
from .service import ControllerService
from elemctl.slogger import configure_logger
from elemctl.controller_protocol import (
    KIND_JSON,
    PROTOCOL_VERSION,
    ROLE_OBSERVER,
    ROLE_WRITER,
    ProtocolReader,
    encode_json,
    parse_json_payload,
)
from elemctl.version import get_runtime_version

log = logging.getLogger(__name__)

_TICK_INTERVAL = 0.020  # ~50Hz


@dataclass
class _ClientConn:
    sock: socket.socket
    reader: ProtocolReader
    desc: str | None
    role: str | None = None
    hello_ok: bool = False


def _peer_label(argv: list[str]) -> str | None:
    if not argv:
        return None

    exe = os.path.basename(argv[0])
    if exe == 'elemctl' and len(argv) > 1:
        sub = argv[1]
        if sub in {'tui', 'sim', 'run', 'web'}:
            return f'elemctl {sub}'
        if sub == 'server':
            return 'elemctl server'

    if '-m' in argv:
        try:
            mod = argv[argv.index('-m') + 1]
        except IndexError:
            mod = ''
        if mod == 'elemctl.tui':
            return 'elemctl tui'
        if mod == 'elemctl.server':
            return 'elemctl server'
        if mod == 'elemctl.sim':
            return 'elemctl sim'
        if mod == 'elemctl.web':
            return 'elemctl web'
        if mod == 'elemctl':
            for arg in argv[argv.index('-m') + 2:]:
                if arg in {'tui', 'sim', 'run', 'web'}:
                    return f'elemctl {arg}'
                if arg == 'server':
                    return 'elemctl server'

    if exe.startswith('python'):
        for arg in argv[1:]:
            if arg in {'tui', 'sim', 'run', 'web'}:
                return f'elemctl {arg}'
            if arg == 'server':
                return 'elemctl server'
            if arg == 'elemctl.tui':
                return 'elemctl tui'
            if arg == 'elemctl.server':
                return 'elemctl server'
            if arg == 'elemctl.sim':
                return 'elemctl sim'
            if arg == 'elemctl.web':
                return 'elemctl web'

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


class ControllerServer:
    """Role-aware multi-client unix socket server wrapping a ControllerService."""

    def __init__(self, service: ControllerService, socket_path: str):
        self._service = service
        self._socket_path = socket_path
        self._running = False

        self._server_sock: socket.socket | None = None
        self._clients: dict[int, _ClientConn] = {}

    def run(self) -> None:
        """Blocking main loop. Returns when shutdown or stopped."""
        # Remove stale socket
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
                self._broadcast(frame_msgs, reliable=False)

                time.sleep(_TICK_INTERVAL)
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
        """Signal the main loop to stop."""
        self._running = False

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _accept_clients(self) -> None:
        if self._server_sock is None:
            return
        while True:
            try:
                conn, _ = self._server_sock.accept()
            except BlockingIOError:
                return
            except OSError:
                return

            conn.setblocking(False)
            client = _ClientConn(
                sock=conn,
                reader=ProtocolReader(),
                desc=_describe_peer(conn),
            )
            self._clients[conn.fileno()] = client
            if client.desc is not None:
                log.info('client connected: %s', client.desc)
            else:
                log.info('client connected')

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
            try:
                client = self._clients.get(sock.fileno())
                if client is None:
                    continue
                data = sock.recv(4096)
            except BlockingIOError:
                continue
            except OSError:
                if client is not None:
                    self._close_client(client)
                continue

            if not data:
                if client is not None:
                    self._close_client(client)
                continue

            client.reader.feed(data)
            close_after_message = False
            for kind, payload in client.reader.messages():
                if not client.hello_ok:
                    close_after_message = self._handle_prehello_message(client, kind, payload)
                    if close_after_message:
                        break
                    continue

                if kind != KIND_JSON:
                    continue
                try:
                    cmd = parse_json_payload(payload)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue

                if cmd.get('cmd') == 'hello':
                    self._send_reply(
                        client,
                        cmd.get('id'),
                        ok=False,
                        error='hello already completed',
                    )
                    continue

                if client.role != ROLE_WRITER:
                    self._send_reply(
                        client,
                        cmd.get('id'),
                        ok=False,
                        error='observer connections cannot send commands',
                    )
                    continue

                reply = self._service.handle_cmd(cmd)
                if not self._send_to_client(client, encode_json(reply), reliable=True):
                    break

            if close_after_message and sock.fileno() in self._clients:
                self._close_client(client)

    def _handle_prehello_message(self, client: _ClientConn, kind: int, payload: bytes) -> bool:
        if kind != KIND_JSON:
            return True

        try:
            cmd = parse_json_payload(payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return True

        cmd_id = cmd.get('id')
        if cmd.get('cmd') != 'hello':
            self._send_reply(
                client,
                cmd_id,
                ok=False,
                error='first command must be hello',
            )
            return True

        if cmd.get('protocol_version') != PROTOCOL_VERSION:
            self._send_reply(
                client,
                cmd_id,
                ok=False,
                error=(
                    f'protocol_version mismatch: expected {PROTOCOL_VERSION}, '
                    f'got {cmd.get("protocol_version")!r}'
                ),
            )
            return True

        role = cmd.get('role')
        if role not in {ROLE_WRITER, ROLE_OBSERVER}:
            self._send_reply(
                client,
                cmd_id,
                ok=False,
                error='role must be "writer" or "observer"',
            )
            return True

        client.role = role
        client.hello_ok = True
        if role == ROLE_WRITER:
            self._service.probe_all()

        self._send_reply(client, cmd_id, ok=True, result={'role': role})
        snapshot = self._service.build_snapshot()
        self._send_to_client(client, encode_json(snapshot), reliable=True)
        return False

    def _broadcast(self, messages: list[bytes], *, reliable: bool) -> None:
        for msg in messages:
            for client in list(self._clients.values()):
                if not client.hello_ok:
                    continue
                self._send_to_client(client, msg, reliable=reliable)

    def _send_reply(
        self,
        client: _ClientConn,
        cmd_id,
        *,
        ok: bool,
        result: dict | None = None,
        error: str | None = None,
    ) -> bool:
        payload = {'type': 'reply', 'id': cmd_id, 'ok': ok}
        if ok:
            payload['result'] = result or {}
        else:
            payload['error'] = error or 'error'
        return self._send_to_client(client, encode_json(payload), reliable=True)

    def _send_to_client(self, client: _ClientConn, data: bytes, *, reliable: bool) -> bool:
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

        # Partial stream writes leave the peer desynchronized; drop the client.
        self._close_client(client)
        return False

    def _close_client(self, client: _ClientConn) -> None:
        fd = client.sock.fileno()
        self._clients.pop(fd, None)
        role = client.role
        desc = client.desc
        try:
            client.sock.close()
        except OSError:
            pass
        if desc is not None:
            if role is not None:
                log.info('client disconnected: %s role=%s', desc, role)
            else:
                log.info('client disconnected: %s', desc)
        else:
            if role is not None:
                log.info('client disconnected: role=%s', role)
            else:
                log.info('client disconnected')


def main() -> None:
    """CLI entry point for elemctl server."""
    parser = argparse.ArgumentParser(
        prog='elemctl server',
        description='Elements controller server',
    )
    parser.add_argument(
        '--config',
        default=None,
        help=f'config JSON path (default: {DEFAULT_CONFIG_PATH})',
    )
    parser.add_argument(
        '--socket', default=DEFAULT_SOCKET_PATH,
        help='unix socket path (default: %(default)s)',
    )
    parser.add_argument(
        '--log-dir', default=None,
        help='logs directory (default: controller.logs_dir or <repo>/logs)',
    )
    parser.add_argument(
        '--debug',
        action='store_true',
        help='enable debug logging',
    )

    args = parser.parse_args()
    log_level = "DEBUG" if args.debug else "INFO"
    configure_logger(level=log_level)

    try:
        config_path = resolve_config_path(args.config)
        config = load_config(config_path)
    except (ConfigError, json.JSONDecodeError) as e:
        log.error('config error: %s', e)
        sys.exit(1)

    log_dir = resolve_runtime_path(args.log_dir, config.logs_dir, DEFAULT_LOGS_PATH)
    configure_logger(
        logfile=os.path.join(log_dir, 'server.log'),
        level=log_level,
    )
    log.info('elements controller started. version: %s', get_runtime_version())
    log.info('using config %s', config_path)

    service = ControllerService(config, config_path=config_path)
    socket_path = os.path.expanduser(args.socket)
    server = ControllerServer(service, socket_path)

    def _on_signal(signum, frame):
        server.shutdown()

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    server.run()


if __name__ == '__main__':
    main()
