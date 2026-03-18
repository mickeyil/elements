"""Observer-only web relay for the Elements controller."""

from __future__ import annotations

import argparse
import asyncio
import base64
import copy
import hashlib
import json
import logging
import mimetypes
import os
import select
import struct
import threading
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_LOGS_PATH,
    DEFAULT_SOCKET_PATH,
    ConfigError,
    load_config,
    resolve_config_path,
    resolve_runtime_path,
)
from .sim_layout import load_layouts_for_devices
from .slogger import configure_logger
from .uds_client import UdsClient
from .uds_wire import KIND_FRAME, KIND_JSON, PROTOCOL_VERSION, ROLE_OBSERVER, parse_json_payload
from .version import get_runtime_version

log = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parent / 'web_static'
_STATIC_ROOT = _STATIC_DIR.resolve()
_WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
_FRAME_HEADER = struct.Struct('<If')
_HTTP_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
}


@dataclass(eq=False)
class _WsClient:
    writer: asyncio.StreamWriter
    peer: str


def _empty_snapshot() -> dict:
    return {
        'type': 'event',
        'event': 'snapshot',
        'protocol_version': PROTOCOL_VERSION,
        'online_count': 0,
        'expected_count': 0,
        'session': None,
        'devices': [],
        'programs': [],
    }


def _relay_status(connected: bool) -> dict:
    return {
        'type': 'event',
        'event': 'relay_status',
        'controller_connected': connected,
    }


def _make_disconnected_snapshot(snapshot: dict | None) -> dict:
    out = copy.deepcopy(snapshot) if snapshot is not None else _empty_snapshot()
    out['type'] = 'event'
    out['event'] = 'snapshot'
    out['protocol_version'] = PROTOCOL_VERSION
    out['session'] = None
    if not isinstance(out.get('programs'), list):
        out['programs'] = []
    devices = out.get('devices')
    if isinstance(devices, list):
        for dev in devices:
            if isinstance(dev, dict):
                dev['connected'] = False
        out['online_count'] = sum(
            1 for dev in devices if isinstance(dev, dict) and dev.get('connected')
        )
        out['expected_count'] = len(devices)
    else:
        out['devices'] = []
        out['online_count'] = 0
        out['expected_count'] = 0
    return out


def _encode_ws_frame(opcode: int, payload: bytes) -> bytes:
    head = bytearray()
    head.append(0x80 | (opcode & 0x0F))
    size = len(payload)
    if size < 126:
        head.append(size)
    elif size < (1 << 16):
        head.extend((126, (size >> 8) & 0xFF, size & 0xFF))
    else:
        head.append(127)
        head.extend(size.to_bytes(8, 'big'))
    return bytes(head) + payload


def _resolve_asset_path(path: str) -> Path | None:
    rel = 'index.html' if path in ('', '/') else unquote(path.lstrip('/'))
    if not rel:
        rel = 'index.html'

    asset_path = (_STATIC_ROOT / rel).resolve()
    try:
        asset_path.relative_to(_STATIC_ROOT)
    except ValueError:
        return None
    if not asset_path.is_file():
        return None
    return asset_path


class WebRelay:
    def __init__(self, socket_path: str, host: str, port: int, layouts: dict[str, dict] | None = None):
        self._socket_path = socket_path
        self._host = host
        self._port = port

        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[tuple[str, object]] | None = None
        self._stop = threading.Event()
        self._uds_thread: threading.Thread | None = None

        self._controller_connected = False
        self._layouts = layouts or {}
        self._snapshot = _empty_snapshot()
        self._snapshot['layouts'] = self._layouts
        self._ws_clients: set[_WsClient] = set()

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._uds_thread = threading.Thread(target=self._uds_reader_loop, daemon=True)
        self._uds_thread.start()

        server = await asyncio.start_server(self._handle_http_client, self._host, self._port)
        sockets = ', '.join(str(sock.getsockname()) for sock in server.sockets or [])
        log.info('web relay listening on %s', sockets)

        broadcast_task = asyncio.create_task(self._broadcast_loop())
        try:
            async with server:
                await server.serve_forever()
        finally:
            self._stop.set()
            server.close()
            await server.wait_closed()
            broadcast_task.cancel()
            await asyncio.gather(broadcast_task, return_exceptions=True)
            if self._uds_thread is not None:
                self._uds_thread.join(timeout=3.0)
            await self._close_all_ws_clients()

    def _uds_reader_loop(self) -> None:
        client: UdsClient | None = None
        last_connected = False

        while not self._stop.is_set():
            if client is None:
                try:
                    client = UdsClient(
                        self._socket_path,
                        timeout=1.0,
                        role=ROLE_OBSERVER,
                    )
                except (ConnectionError, OSError):
                    if last_connected:
                        last_connected = False
                        self._enqueue_status(False)
                    self._stop.wait(1.0)
                    continue

                if not last_connected:
                    last_connected = True
                    self._enqueue_status(True)
                try:
                    self._enqueue_uds_messages(client.recv_once())
                except (ConnectionError, OSError):
                    client.close()
                    client = None
                    if last_connected:
                        last_connected = False
                        self._enqueue_status(False)
                    continue

            try:
                readable, _, _ = select.select([client.fileno()], [], [], 0.5)
            except (OSError, ValueError):
                readable = []

            if not readable:
                if self._stop.is_set():
                    break
                continue

            try:
                self._enqueue_uds_messages(client.recv_once())
            except (ConnectionError, OSError):
                client.close()
                client = None
                if last_connected:
                    last_connected = False
                    self._enqueue_status(False)

        if client is not None:
            client.close()

    def _enqueue_status(self, connected: bool) -> None:
        if self._loop is None or self._queue is None:
            return
        self._loop.call_soon_threadsafe(
            self._queue.put_nowait,
            ('relay_status', _relay_status(connected)),
        )

    def _enqueue_uds_messages(self, messages: list[tuple[int, bytes]]) -> None:
        if self._loop is None or self._queue is None:
            return
        for kind, payload in messages:
            if kind == KIND_JSON:
                try:
                    msg = parse_json_payload(payload)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                self._loop.call_soon_threadsafe(self._queue.put_nowait, ('json', msg))
            elif kind == KIND_FRAME:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, ('frame', payload))

    async def _broadcast_loop(self) -> None:
        assert self._queue is not None
        while True:
            item_type, payload = await self._queue.get()
            if item_type == 'relay_status':
                msg = payload
                assert isinstance(msg, dict)
                connected = bool(msg.get('controller_connected'))
                self._controller_connected = connected
                await self._broadcast_json(msg)
                if not connected:
                    self._snapshot = _make_disconnected_snapshot(self._snapshot)
                    self._snapshot['layouts'] = self._layouts
                    await self._broadcast_json(self._snapshot)
                continue

            if item_type == 'json':
                msg = payload
                assert isinstance(msg, dict)
                self._apply_json_message(msg)
                if msg.get('type') == 'event' and msg.get('event') == 'snapshot':
                    await self._broadcast_json(self._snapshot)
                else:
                    await self._broadcast_json(msg)
                continue

            if item_type == 'frame':
                frame = payload
                assert isinstance(frame, bytes)
                self._apply_frame(frame)
                await self._broadcast_binary(frame)

    def _apply_json_message(self, msg: dict) -> None:
        if msg.get('type') != 'event':
            return

        event = msg.get('event')
        if event == 'snapshot':
            self._snapshot = copy.deepcopy(msg)
            self._snapshot['layouts'] = self._layouts
            return

        if event == 'session_start':
            self._snapshot['session'] = {
                'session_id': msg.get('session_id'),
                'epoch': msg.get('epoch'),
                'playback_state': 'loaded',
                'duration': msg.get('duration'),
                'current_t_rel': 0.0,
                'safe_intervals': msg.get('safe_intervals', []),
                'strips': msg.get('strips', []),
            }
            return

        if event == 'state':
            session = self._snapshot.get('session')
            if isinstance(session, dict):
                session['playback_state'] = msg.get('state')
                if 'epoch' in msg:
                    session['epoch'] = msg.get('epoch')
            return

        if event == 'loop':
            session = self._snapshot.get('session')
            if isinstance(session, dict):
                session['epoch'] = msg.get('epoch')
                session['current_t_rel'] = 0.0
            return

        if event == 'device_status':
            devices = self._snapshot.get('devices')
            if isinstance(devices, list):
                for dev in devices:
                    if (
                        isinstance(dev, dict)
                        and dev.get('device_id') == msg.get('device_id')
                    ):
                        dev['connected'] = bool(msg.get('connected'))
                        break
                self._snapshot['online_count'] = sum(
                    1 for dev in devices if isinstance(dev, dict) and dev.get('connected')
                )
            return

        if event == 'programs_updated':
            programs = msg.get('programs')
            if isinstance(programs, list):
                self._snapshot['programs'] = copy.deepcopy(programs)

    def _apply_frame(self, payload: bytes) -> None:
        session = self._snapshot.get('session')
        if not isinstance(session, dict):
            return
        if len(payload) < _FRAME_HEADER.size:
            return
        _frame_index, t_rel = _FRAME_HEADER.unpack_from(payload, 0)
        session['current_t_rel'] = t_rel

    async def _handle_http_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            request = await reader.readuntil(b'\r\n\r\n')
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            writer.close()
            await writer.wait_closed()
            return

        try:
            head = request.decode('iso-8859-1')
        except UnicodeDecodeError:
            await self._write_http_response(writer, 400, b'bad request')
            return

        lines = head.split('\r\n')
        if not lines or len(lines[0].split()) != 3:
            await self._write_http_response(writer, 400, b'bad request')
            return

        method, target, _version = lines[0].split()
        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line:
                continue
            if ':' not in line:
                continue
            name, value = line.split(':', 1)
            headers[name.strip().lower()] = value.strip()

        if method != 'GET':
            await self._write_http_response(writer, 405, b'method not allowed')
            return

        path = urlsplit(target).path or '/'
        if path == '/ws':
            await self._handle_ws(reader, writer, headers)
            return

        await self._serve_asset(path, writer)

    async def _handle_ws(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        headers: dict[str, str],
    ) -> None:
        key = headers.get('sec-websocket-key')
        upgrade = headers.get('upgrade', '').lower()
        if upgrade != 'websocket' or not key:
            await self._write_http_response(writer, 400, b'bad websocket request')
            return

        accept = base64.b64encode(
            hashlib.sha1((key + _WS_GUID).encode('ascii')).digest()
        ).decode('ascii')

        response = (
            'HTTP/1.1 101 Switching Protocols\r\n'
            'Upgrade: websocket\r\n'
            'Connection: Upgrade\r\n'
            f'Sec-WebSocket-Accept: {accept}\r\n'
            '\r\n'
        ).encode('ascii')
        writer.write(response)
        await writer.drain()

        peer = str(writer.get_extra_info('peername') or 'browser')
        client = _WsClient(writer=writer, peer=peer)
        self._ws_clients.add(client)

        try:
            await self._send_json(client, _relay_status(self._controller_connected))
            await self._send_json(client, self._snapshot)

            while not reader.at_eof():
                data = await reader.read(1024)
                if not data:
                    break
        finally:
            self._ws_clients.discard(client)
            writer.close()
            await writer.wait_closed()

    async def _serve_asset(self, path: str, writer: asyncio.StreamWriter) -> None:
        asset_path = _resolve_asset_path(path)
        if asset_path is None:
            await self._write_http_response(writer, 404, b'not found')
            return

        try:
            body = asset_path.read_bytes()
        except OSError:
            await self._write_http_response(writer, 500, b'missing asset')
            return

        content_type = _HTTP_TYPES.get(asset_path.suffix)
        if content_type is None:
            content_type = mimetypes.guess_type(str(asset_path))[0] or 'application/octet-stream'

        await self._write_http_response(
            writer,
            200,
            body,
            content_type=content_type,
        )

    async def _write_http_response(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        body: bytes,
        *,
        content_type: str = 'text/plain; charset=utf-8',
    ) -> None:
        reasons = {
            200: 'OK',
            400: 'Bad Request',
            404: 'Not Found',
            405: 'Method Not Allowed',
            500: 'Internal Server Error',
        }
        head = (
            f'HTTP/1.1 {status} {reasons.get(status, "OK")}\r\n'
            f'Content-Type: {content_type}\r\n'
            f'Content-Length: {len(body)}\r\n'
            'Cache-Control: no-store\r\n'
            'Connection: close\r\n'
            '\r\n'
        ).encode('ascii')
        writer.write(head + body)
        try:
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    async def _broadcast_json(self, msg: dict) -> None:
        dead: list[_WsClient] = []
        for client in list(self._ws_clients):
            try:
                await self._send_json(client, msg)
            except (ConnectionError, OSError, asyncio.TimeoutError):
                dead.append(client)
        for client in dead:
            self._ws_clients.discard(client)
            client.writer.close()
            await client.writer.wait_closed()

    async def _broadcast_binary(self, payload: bytes) -> None:
        dead: list[_WsClient] = []
        for client in list(self._ws_clients):
            try:
                await self._send_binary(client, payload)
            except (ConnectionError, OSError, asyncio.TimeoutError):
                dead.append(client)
        for client in dead:
            self._ws_clients.discard(client)
            client.writer.close()
            await client.writer.wait_closed()

    async def _send_json(self, client: _WsClient, msg: dict) -> None:
        payload = json.dumps(msg, separators=(',', ':')).encode('utf-8')
        await self._send_ws_payload(client, 0x1, payload)

    async def _send_binary(self, client: _WsClient, payload: bytes) -> None:
        await self._send_ws_payload(client, 0x2, payload)

    async def _send_ws_payload(self, client: _WsClient, opcode: int, payload: bytes) -> None:
        client.writer.write(_encode_ws_frame(opcode, payload))
        await asyncio.wait_for(client.writer.drain(), timeout=0.1)

    async def _close_all_ws_clients(self) -> None:
        for client in list(self._ws_clients):
            self._ws_clients.discard(client)
            client.writer.close()
            try:
                await client.writer.wait_closed()
            except OSError:
                pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='elemctl web',
        description='Elements web relay and realtime viewer',
    )
    parser.add_argument(
        '--socket', default=DEFAULT_SOCKET_PATH,
        help='controller UDS socket path (default: %(default)s)',
    )
    parser.add_argument(
        '--config', default=DEFAULT_CONFIG_PATH,
        help='config JSON path (default: %(default)s)',
    )
    parser.add_argument(
        '--host', default='0.0.0.0',
        help='HTTP bind host (default: %(default)s)',
    )
    parser.add_argument(
        '--port', type=int, default=8080,
        help='HTTP port (default: %(default)s)',
    )
    parser.add_argument(
        '--log-dir', default=None,
        help='logs directory (default: controller.logs_dir or <repo>/logs)',
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    configure_logger(level='INFO')

    try:
        config_path = resolve_config_path(args.config)
        config = load_config(config_path)
    except (ConfigError, json.JSONDecodeError) as e:
        log.error('config error: %s', e)
        raise SystemExit(1)

    log_dir = resolve_runtime_path(args.log_dir, config.logs_dir, DEFAULT_LOGS_PATH)
    configure_logger(
        logfile=os.path.join(log_dir, 'web.log'),
        level='INFO',
    )
    log.info('elements web relay started. version: %s', get_runtime_version())
    log.info('using config %s', config_path)
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    layouts = load_layouts_for_devices(config.devices)

    relay = WebRelay(os.path.expanduser(args.socket), args.host, args.port, layouts)

    try:
        asyncio.run(relay.run())
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
