"""Web UI server for the Elements controller."""

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
import shutil
import signal
import struct
import subprocess
import threading
import time
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
from .sim_layout import (
    DEFAULT_LAYOUTS_PATH,
    LayoutError,
    load_layout_for_editor,
    load_layouts_for_devices,
    save_layout_for_editor,
)
from .slogger import configure_logger
from .controller_client import ControllerClient
from .controller_protocol import (
    KIND_FRAME,
    KIND_JSON,
    PROTOCOL_VERSION,
    ROLE_OBSERVER,
    ROLE_WRITER,
    parse_json_payload,
)
from .version import get_runtime_version

log = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).resolve().parents[1] / 'web_ui' / 'dist'
_STATIC_ROOT = _STATIC_DIR.resolve()
_WS_GUID = '258EAFA5-E914-47DA-95CA-C5AB0DC85B11'
_FRAME_HEADER = struct.Struct('<If')
_MAX_HTTP_BODY = 1 << 20
_WS_CLOSE_TIMEOUT = 0.25
_CONTROLLER_REPLY_TIMEOUT = 2.0
_HTTP_TYPES = {
    '.html': 'text/html; charset=utf-8',
    '.js': 'application/javascript; charset=utf-8',
    '.css': 'text/css; charset=utf-8',
    '.woff2': 'font/woff2',
}


@dataclass(eq=False)
class _WsClient:
    writer: asyncio.StreamWriter
    peer: str


@dataclass
class _HttpError(Exception):
    status: int
    message: str


def _empty_snapshot() -> dict:
    return {
        'type': 'event',
        'event': 'snapshot',
        'protocol_version': PROTOCOL_VERSION,
        'server_version': None,
        'online_count': 0,
        'expected_count': 0,
        'session': None,
        'devices': [],
        'programs': [],
    }


def _server_status(connected: bool) -> dict:
    return {
        'type': 'event',
        'event': 'server_status',
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


def _static_cache_control(asset_path: Path) -> str:
    assets_dir = _STATIC_ROOT / 'assets'
    if asset_path.parent == assets_dir:
        return 'public, max-age=31536000, immutable'
    return 'no-store'


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


def _layout_device_uid_from_path(path: str) -> str | None:
    prefix = '/api/layouts/'
    if not path.startswith(prefix):
        return None
    device_uid = unquote(path[len(prefix):])
    if not device_uid or '/' in device_uid:
        return None
    return device_uid


def _is_device_api_path(path: str) -> bool:
    return path == '/api/devices' or _device_uid_from_path(path) is not None


def _device_uid_from_path(path: str) -> str | None:
    prefix = '/api/devices/'
    if not path.startswith(prefix):
        return None
    device_uid = unquote(path[len(prefix):])
    if not device_uid or '/' in device_uid:
        return None
    return device_uid


def _tailscale_urls(port: int) -> list[str]:
    if port <= 0:
        return []
    if shutil.which('tailscale') is None:
        return []

    try:
        result = subprocess.run(
            ['tailscale', 'ip', '-4'],
            check=False,
            capture_output=True,
            text=True,
            timeout=1.0,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    if result.returncode != 0:
        return []

    urls: list[str] = []
    for line in result.stdout.splitlines():
        ip = line.strip()
        if ip:
            urls.append(f'http://{ip}:{port}/')
    return urls


class WebUiServer:
    def __init__(
        self,
        socket_path: str,
        host: str,
        port: int,
        layouts: dict[str, dict] | None = None,
        sim_devices: dict[str, int] | None = None,
        layouts_dir: str = DEFAULT_LAYOUTS_PATH,
        server_version: str | None = None,
    ):
        self._socket_path = socket_path
        self._host = host
        self._port = port

        self._loop: asyncio.AbstractEventLoop | None = None
        self._queue: asyncio.Queue[tuple[str, object]] | None = None
        self._stop = threading.Event()
        self._controller_thread: threading.Thread | None = None

        self._controller_connected = False
        self._server_version = server_version
        self._layouts = layouts or {}
        self._sim_devices = sim_devices or {}
        self._layouts_dir = os.path.expanduser(layouts_dir)
        self._snapshot = _empty_snapshot()
        self._snapshot['server_version'] = self._server_version
        self._snapshot['layouts'] = self._layouts
        self._ws_clients: set[_WsClient] = set()
        self._ws_tasks: set[asyncio.Task] = set()
        # Set while any browser is connected; the reader thread keeps the
        # controller's frame subscription in step with it, so frames stream
        # only when someone is watching.
        self._want_frames = threading.Event()

    async def run(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._queue = asyncio.Queue()
        self._controller_thread = threading.Thread(target=self._controller_reader_loop, daemon=True)
        self._controller_thread.start()

        server = await asyncio.start_server(self._handle_http_client, self._host, self._port)
        sockets = ', '.join(str(sock.getsockname()) for sock in server.sockets or [])
        log.info('web UI server listening on %s', sockets)
        if server.sockets:
            sockname = server.sockets[0].getsockname()
            if isinstance(sockname, tuple) and len(sockname) >= 2:
                for url in _tailscale_urls(int(sockname[1])):
                    log.info('tailscale viewer url: %s', url)

        stop_requested = asyncio.Event()
        installed_handlers: list[signal.Signals] = []

        def _request_shutdown() -> None:
            if not stop_requested.is_set():
                log.info('shutdown requested')
                stop_requested.set()

        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                try:
                    self._loop.add_signal_handler(signum, _request_shutdown)
                    installed_handlers.append(signum)
                except (NotImplementedError, RuntimeError):
                    pass

        broadcast_task = asyncio.create_task(self._broadcast_loop())
        try:
            await stop_requested.wait()
        finally:
            await self._shutdown(server, broadcast_task, installed_handlers)

    async def _shutdown(
        self,
        server: asyncio.AbstractServer,
        broadcast_task: asyncio.Task,
        installed_handlers: list[signal.Signals],
    ) -> None:
        if self._loop is not None:
            for signum in installed_handlers:
                self._loop.remove_signal_handler(signum)
        self._stop.set()
        server.close()

        ws_tasks = list(self._ws_tasks)
        for task in ws_tasks:
            task.cancel()
        if ws_tasks:
            await asyncio.gather(*ws_tasks, return_exceptions=True)

        broadcast_task.cancel()
        await asyncio.gather(broadcast_task, return_exceptions=True)
        await server.wait_closed()

        if self._controller_thread is not None:
            self._controller_thread.join(timeout=3.0)
        await self._close_all_ws_clients()

    def _controller_reader_loop(self) -> None:
        client: ControllerClient | None = None
        last_connected = False
        subscribed = False

        while not self._stop.is_set():
            if client is None:
                try:
                    client = ControllerClient(
                        self._socket_path,
                        timeout=1.0,
                        role=ROLE_OBSERVER,
                    )
                except (ConnectionError, OSError):
                    if last_connected:
                        log.info('controller disconnected: %s', self._socket_path)
                        last_connected = False
                        self._enqueue_status(False)
                    self._stop.wait(1.0)
                    continue

                subscribed = False   # fresh connection: re-apply any subscription
                if not last_connected:
                    log.info('controller connected: %s', self._socket_path)
                    last_connected = True
                    self._enqueue_status(True)
                try:
                    self._enqueue_controller_messages(client.recv_once())
                except (ConnectionError, OSError):
                    client.close()
                    client = None
                    if last_connected:
                        log.info('controller disconnected: %s', self._socket_path)
                        last_connected = False
                        self._enqueue_status(False)
                    continue

            subscribed = self._sync_subscription(client, subscribed)
            try:
                readable, _, _ = select.select([client.fileno()], [], [], 0.5)
            except (OSError, ValueError):
                readable = []

            if not readable:
                if self._stop.is_set():
                    break
                continue

            try:
                self._enqueue_controller_messages(client.recv_once())
            except (ConnectionError, OSError):
                client.close()
                client = None
                if last_connected:
                    log.info('controller disconnected: %s', self._socket_path)
                    last_connected = False
                    self._enqueue_status(False)

        if client is not None:
            client.close()

    def _sync_subscription(self, client: ControllerClient, subscribed: bool) -> bool:
        """Bring the controller's frame subscription in line with whether any
        browser is connected. Runs in the reader thread, which owns the client;
        a send failure leaves the reconnect path to retry."""
        desired = self._want_frames.is_set()
        if desired == subscribed:
            return subscribed
        cmd = 'subscribe_frames' if desired else 'unsubscribe_frames'
        try:
            client.send_cmd({'id': client.next_id(), 'cmd': cmd})
        except OSError:
            return subscribed
        return desired

    def _enqueue_status(self, connected: bool) -> None:
        if self._loop is None or self._queue is None:
            return
        self._loop.call_soon_threadsafe(
            self._queue.put_nowait,
            ('server_status', _server_status(connected)),
        )

    def _enqueue_controller_messages(self, messages: list[tuple[int, bytes]]) -> None:
        if self._loop is None or self._queue is None:
            return
        for kind, payload in messages:
            if kind == KIND_JSON:
                try:
                    msg = parse_json_payload(payload)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                # Replies answer this relay's own subscribe/unsubscribe; they
                # are internal control traffic, not browser-facing events.
                if msg.get('type') == 'reply':
                    continue
                self._loop.call_soon_threadsafe(self._queue.put_nowait, ('json', msg))
            elif kind == KIND_FRAME:
                self._loop.call_soon_threadsafe(self._queue.put_nowait, ('frame', payload))

    async def _broadcast_loop(self) -> None:
        assert self._queue is not None
        while True:
            item_type, payload = await self._queue.get()
            if item_type == 'server_status':
                msg = payload
                assert isinstance(msg, dict)
                connected = bool(msg.get('controller_connected'))
                self._controller_connected = connected
                await self._broadcast_json(msg)
                if not connected:
                    self._snapshot = _make_disconnected_snapshot(self._snapshot)
                    self._snapshot['server_version'] = self._server_version
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
            self._snapshot['server_version'] = self._server_version
            self._snapshot['layouts'] = self._layouts
            return

        if event == 'session_start':
            self._snapshot['session'] = {
                'session_id': msg.get('session_id'),
                'epoch': msg.get('epoch'),
                'playback_state': 'loaded',
                'duration': msg.get('duration'),
                'current_t_rel': 0.0,
                'observer_suspended': msg.get('observer_suspended'),
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
                if 'observer_suspended' in msg:
                    session['observer_suspended'] = msg.get('observer_suspended')
            return

        if event == 'loop':
            session = self._snapshot.get('session')
            if isinstance(session, dict):
                session['epoch'] = msg.get('epoch')
                session['current_t_rel'] = 0.0
                if 'observer_suspended' in msg:
                    session['observer_suspended'] = msg.get('observer_suspended')
            return

        if event == 'device_status':
            devices = self._snapshot.get('devices')
            if isinstance(devices, list):
                for dev in devices:
                    if (
                        isinstance(dev, dict)
                        and dev.get('device_id') == msg.get('device_id')
                    ):
                        if 'connected' in msg:
                            dev['connected'] = bool(msg.get('connected'))
                        if 'last_seen' in msg:
                            dev['last_seen'] = msg.get('last_seen')
                        for key in (
                            'session_role',
                            'reported',
                            'reported_at',
                        ):
                            if key in msg:
                                dev[key] = copy.deepcopy(msg.get(key))
                        for key in (
                            'clock_state',
                            'clock_offset_ms',
                            'clock_rtt_ms',
                            'clock_last_sync_age_s',
                        ):
                            if key in msg:
                                dev[key] = msg.get(key)
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
            try:
                request = await reader.readuntil(b'\r\n\r\n')
            except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
                await self._close_http_writer(writer)
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

            path = urlsplit(target).path or '/'
            if path == '/ws':
                if method != 'GET':
                    await self._write_http_response(writer, 405, b'method not allowed')
                    return
                await self._handle_ws(reader, writer, headers)
                return

            if _layout_device_uid_from_path(path) is not None:
                await self._handle_layout_api(method, path, headers, reader, writer)
                return

            if _is_device_api_path(path):
                await self._handle_device_api(method, path, headers, reader, writer)
                return

            if method != 'GET':
                await self._write_http_response(writer, 405, b'method not allowed')
                return

            await self._serve_asset(path, writer)
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError, asyncio.TimeoutError):
            return

    async def _handle_layout_api(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        device_uid = _layout_device_uid_from_path(path)
        if device_uid is None:
            await self._write_json_response(writer, 404, {'error': 'not found'})
            return

        if method == 'GET':
            status, payload = self._get_layout_response(device_uid)
            await self._write_json_response(writer, status, payload)
            return

        if method != 'POST':
            await self._write_http_response(writer, 405, b'method not allowed')
            return

        try:
            body = await self._read_http_body(reader, headers)
            payload = json.loads(body.decode('utf-8'))
        except _HttpError as e:
            await self._write_json_response(writer, e.status, {'error': e.message})
            return
        except (UnicodeDecodeError, json.JSONDecodeError):
            await self._write_json_response(writer, 400, {'error': 'invalid json body'})
            return

        status, response = self._save_layout_response(device_uid, payload)
        await self._write_json_response(writer, status, response)
        if status == 200:
            await self._broadcast_json(self._snapshot)

    async def _handle_device_api(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        if method == 'POST' and path == '/api/devices':
            pass
        elif method == 'PATCH' and _device_uid_from_path(path) is not None:
            pass
        elif method == 'DELETE' and _device_uid_from_path(path) is not None:
            pass
        else:
            await self._write_http_response(writer, 405, b'method not allowed')
            return

        if method == 'POST' and path == '/api/devices':
            try:
                body = await self._read_http_body(reader, headers)
                payload = json.loads(body.decode('utf-8'))
            except _HttpError as e:
                await self._write_json_response(writer, e.status, {'error': e.message})
                return
            except (UnicodeDecodeError, json.JSONDecodeError):
                await self._write_json_response(writer, 400, {'error': 'invalid json body'})
                return
            status, response = self._create_device_response(payload)
            await self._write_json_response(writer, status, response)
            return

        device_uid = _device_uid_from_path(path)
        if method == 'PATCH' and device_uid is not None:
            try:
                body = await self._read_http_body(reader, headers)
                payload = json.loads(body.decode('utf-8'))
            except _HttpError as e:
                await self._write_json_response(writer, e.status, {'error': e.message})
                return
            except (UnicodeDecodeError, json.JSONDecodeError):
                await self._write_json_response(writer, 400, {'error': 'invalid json body'})
                return
            status, response = self._edit_device_response(device_uid, payload)
            await self._write_json_response(writer, status, response)
            return

        if method == 'DELETE' and device_uid is not None:
            status, response = self._remove_device_response(device_uid)
            await self._write_json_response(writer, status, response)
            return

        await self._write_http_response(writer, 405, b'method not allowed')

    def _remove_device_response(self, device_uid: str) -> tuple[int, dict]:
        if not isinstance(device_uid, str) or not device_uid:
            return 400, {'error': 'device uid must be a non-empty string'}

        try:
            reply = self._send_controller_cmd({
                'cmd': 'remove_device',
                'device_uid': device_uid,
            })
        except _HttpError as e:
            return e.status, {'error': e.message}

        if not reply.get('ok'):
            error = reply.get('error')
            return 400, {'error': error if isinstance(error, str) else 'controller command failed'}

        result = reply.get('result')
        return 200, {'ok': True, 'result': result if isinstance(result, dict) else {}}

    def _edit_device_response(self, target_device_uid: str, payload: object) -> tuple[int, dict]:
        if not isinstance(payload, dict):
            return 400, {'error': 'request body must be a JSON object'}
        if not isinstance(target_device_uid, str) or not target_device_uid:
            return 400, {'error': 'target device uid must be a non-empty string'}

        device_uid = payload.get('device_uid')
        strip_id = payload.get('strip_id')
        length = payload.get('length')

        if not isinstance(device_uid, str) or not device_uid:
            return 400, {'error': 'device_uid must be a non-empty string'}
        if not isinstance(strip_id, str) or not strip_id:
            return 400, {'error': 'strip_id must be a non-empty string'}
        if not isinstance(length, int) or isinstance(length, bool) or length < 1:
            return 400, {'error': 'length must be a positive integer'}

        try:
            reply = self._send_controller_cmd({
                'cmd': 'edit_device',
                'target_device_uid': target_device_uid,
                'device_uid': device_uid,
                'strip_id': strip_id,
                'length': length,
            })
        except _HttpError as e:
            return e.status, {'error': e.message}

        if not reply.get('ok'):
            error = reply.get('error')
            return 400, {'error': error if isinstance(error, str) else 'controller command failed'}

        result = reply.get('result')
        return 200, {'ok': True, 'result': result if isinstance(result, dict) else {}}

    def _create_device_response(self, payload: object) -> tuple[int, dict]:
        if not isinstance(payload, dict):
            return 400, {'error': 'request body must be a JSON object'}

        device_type = payload.get('device_type')
        device_uid = payload.get('device_uid')
        strip_id = payload.get('strip_id')
        length = payload.get('length')

        if device_type not in {'sim', 'esp32'}:
            return 400, {'error': "device_type must be 'sim' or 'esp32'"}
        if not isinstance(device_uid, str) or not device_uid:
            return 400, {'error': 'device_uid must be a non-empty string'}
        if not isinstance(strip_id, str) or not strip_id:
            return 400, {'error': 'strip_id must be a non-empty string'}
        if not isinstance(length, int) or isinstance(length, bool) or length < 1:
            return 400, {'error': 'length must be a positive integer'}

        try:
            reply = self._send_controller_cmd({
                'cmd': 'add_device',
                'device_type': device_type,
                'device_uid': device_uid,
                'strip_id': strip_id,
                'length': length,
            })
        except _HttpError as e:
            return e.status, {'error': e.message}

        if not reply.get('ok'):
            error = reply.get('error')
            return 400, {'error': error if isinstance(error, str) else 'controller command failed'}

        result = reply.get('result')
        return 200, {'ok': True, 'result': result if isinstance(result, dict) else {}}

    def _get_layout_response(self, device_uid: str) -> tuple[int, dict]:
        configured_length = self._sim_devices.get(device_uid)
        if configured_length is None:
            return 400, {'error': 'unknown sim device'}

        try:
            payload = load_layout_for_editor(device_uid, configured_length, self._layouts_dir)
        except (LayoutError, OSError) as e:
            return 500, {'error': f'failed to load layout: {e}'}

        if payload is None:
            return 404, {'error': 'layout not found'}
        return 200, payload

    def _save_layout_response(self, device_uid: str, payload: object) -> tuple[int, dict]:
        configured_length = self._sim_devices.get(device_uid)
        if configured_length is None:
            return 400, {'error': 'unknown sim device'}
        if not isinstance(payload, dict):
            return 400, {'error': 'request body must be a JSON object'}

        base_csv_hash = payload.get('base_csv_hash')
        if base_csv_hash is not None and not isinstance(base_csv_hash, str):
            return 400, {'error': 'base_csv_hash must be a string or null'}

        try:
            saved = save_layout_for_editor(
                device_uid,
                configured_length,
                self._layouts_dir,
                payload.get('rows'),
                payload.get('editor'),
            )
        except LayoutError as e:
            return 400, {'error': str(e)}
        except OSError as e:
            return 500, {'error': f'failed to write layout: {e}'}

        self._layouts[device_uid] = {'rows': saved['rows']}
        self._snapshot['layouts'] = self._layouts
        return 200, saved

    async def _read_http_body(
        self,
        reader: asyncio.StreamReader,
        headers: dict[str, str],
    ) -> bytes:
        raw_length = headers.get('content-length')
        if raw_length is None:
            raise _HttpError(400, 'missing content-length')
        try:
            content_length = int(raw_length)
        except ValueError as e:
            raise _HttpError(400, 'invalid content-length') from e
        if content_length < 0:
            raise _HttpError(400, 'invalid content-length')
        if content_length > _MAX_HTTP_BODY:
            raise _HttpError(413, 'request body too large')
        try:
            return await reader.readexactly(content_length)
        except asyncio.IncompleteReadError as e:
            raise _HttpError(400, 'incomplete request body') from e

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
        if len(self._ws_clients) == 1:
            self._want_frames.set()      # first viewer: ask the controller for frames
        task = asyncio.current_task()
        if task is not None:
            self._ws_tasks.add(task)

        try:
            await self._send_json(client, _server_status(self._controller_connected))
            await self._send_json(client, self._snapshot)

            while not reader.at_eof():
                data = await reader.read(1024)
                if not data:
                    break
        finally:
            if task is not None:
                self._ws_tasks.discard(task)
            self._ws_clients.discard(client)
            if not self._ws_clients:
                self._want_frames.clear()    # last viewer gone: stop frames
            await self._close_ws_writer(writer)

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
            cache_control=_static_cache_control(asset_path),
        )

    async def _write_json_response(self, writer: asyncio.StreamWriter, status: int, payload: dict) -> None:
        await self._write_http_response(
            writer,
            status,
            json.dumps(payload, separators=(',', ':')).encode('utf-8'),
            content_type='application/json; charset=utf-8',
        )

    async def _write_http_response(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        body: bytes,
        *,
        content_type: str = 'text/plain; charset=utf-8',
        cache_control: str = 'no-store',
    ) -> None:
        reasons = {
            200: 'OK',
            400: 'Bad Request',
            404: 'Not Found',
            405: 'Method Not Allowed',
            413: 'Payload Too Large',
            503: 'Service Unavailable',
            500: 'Internal Server Error',
        }
        head = (
            f'HTTP/1.1 {status} {reasons.get(status, "OK")}\r\n'
            f'Content-Type: {content_type}\r\n'
            f'Content-Length: {len(body)}\r\n'
            f'Cache-Control: {cache_control}\r\n'
            'Connection: close\r\n'
            '\r\n'
        ).encode('ascii')
        writer.write(head + body)
        try:
            await writer.drain()
        finally:
            await self._close_http_writer(writer)

    async def _broadcast_json(self, msg: dict) -> None:
        dead: list[_WsClient] = []
        for client in list(self._ws_clients):
            try:
                await self._send_json(client, msg)
            except (ConnectionError, OSError, asyncio.TimeoutError):
                dead.append(client)
        for client in dead:
            self._ws_clients.discard(client)
            await self._close_ws_writer(client.writer)

    async def _broadcast_binary(self, payload: bytes) -> None:
        dead: list[_WsClient] = []
        for client in list(self._ws_clients):
            try:
                await self._send_binary(client, payload)
            except (ConnectionError, OSError, asyncio.TimeoutError):
                dead.append(client)
        for client in dead:
            self._ws_clients.discard(client)
            await self._close_ws_writer(client.writer)

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
            await self._close_ws_writer(client.writer)

    async def _close_http_writer(self, writer: asyncio.StreamWriter) -> None:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            return

    async def _close_ws_writer(self, writer: asyncio.StreamWriter) -> None:
        writer.close()
        try:
            await asyncio.wait_for(writer.wait_closed(), timeout=_WS_CLOSE_TIMEOUT)
            return
        except asyncio.CancelledError:
            self._abort_ws_writer(writer)
            raise
        except (asyncio.TimeoutError, ConnectionError, OSError):
            self._abort_ws_writer(writer)

    @staticmethod
    def _abort_ws_writer(writer: asyncio.StreamWriter) -> None:
        transport = getattr(writer, 'transport', None)
        if transport is not None:
            transport.abort()

    def _send_controller_cmd(self, cmd: dict) -> dict:
        try:
            client = ControllerClient(self._socket_path, timeout=1.5, role=ROLE_WRITER)
        except (ConnectionError, OSError) as e:
            raise _HttpError(503, f'controller unavailable: {e}') from e

        try:
            cmd_id = client.next_id()
            client.send_cmd({**cmd, 'id': cmd_id})
            deadline = time.monotonic() + _CONTROLLER_REPLY_TIMEOUT
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _HttpError(503, 'controller timed out')

                sock = getattr(client, '_sock', None)
                previous_timeout = None
                if sock is not None:
                    previous_timeout = sock.gettimeout()
                    sock.settimeout(min(previous_timeout or remaining, remaining))
                try:
                    messages = client.recv_once()
                except TimeoutError as e:
                    raise _HttpError(503, 'controller timed out') from e
                except (ConnectionError, OSError) as e:
                    raise _HttpError(503, f'controller unavailable: {e}') from e
                finally:
                    if sock is not None and previous_timeout is not None:
                        sock.settimeout(previous_timeout)
                for kind, payload in messages:
                    if kind != KIND_JSON:
                        continue
                    try:
                        msg = parse_json_payload(payload)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if msg.get('type') == 'reply' and msg.get('id') == cmd_id:
                        return msg
        finally:
            client.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='elemctl web',
        description='Elements web UI server and realtime viewer',
    )
    parser.add_argument(
        '--socket', default=DEFAULT_SOCKET_PATH,
        help='controller unix socket path (default: %(default)s)',
    )
    parser.add_argument(
        '--config',
        default=None,
        help=f'config JSON path (default: {DEFAULT_CONFIG_PATH})',
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
    runtime_version = get_runtime_version()
    log.info('elements web UI server started. version: %s', runtime_version)
    log.info('using config %s', config_path)
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    layouts_dir = os.path.expanduser(DEFAULT_LAYOUTS_PATH)
    layouts = load_layouts_for_devices(config.devices, layouts_dir=layouts_dir)
    sim_devices = {dc.device_uid: dc.length for dc in config.devices if dc.device_type == 'sim'}

    server = WebUiServer(
        os.path.expanduser(args.socket),
        args.host,
        args.port,
        layouts,
        sim_devices,
        layouts_dir,
        runtime_version,
    )

    try:
        asyncio.run(server.run())
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
