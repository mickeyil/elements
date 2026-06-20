import asyncio
import json
import logging
import signal
import elemctl.web as web_mod
import pytest
from elemctl.controller_protocol import PROTOCOL_VERSION
from elemctl.web import (
    WebUiServer,
    _build_parser,
    _device_uid_from_path,
    _encode_ws_frame,
    _layout_device_uid_from_path,
    _make_disconnected_snapshot,
    _program_id_from_load_path,
    _resolve_asset_path,
    _session_verb_from_path,
    _static_cache_control,
)


def test_encode_ws_frame_small_payload():
    payload = b'hi'
    frame = _encode_ws_frame(0x1, payload)

    assert frame[:2] == b'\x81\x02'
    assert frame[2:] == payload


def test_encode_ws_frame_extended_payload():
    payload = b'a' * 130
    frame = _encode_ws_frame(0x2, payload)

    assert frame[0] == 0x82
    assert frame[1] == 126
    assert frame[2:4] == (130).to_bytes(2, 'big')
    assert frame[4:] == payload


def test_make_disconnected_snapshot_offlines_configured_and_drops_discovered():
    snap = {
        'type': 'event',
        'event': 'snapshot',
        'protocol_version': PROTOCOL_VERSION,
        'online_count': 1,
        'expected_count': 1,
        'session': {'state': 'playing'},
        'devices': [
            {'uid': 'sim-a', 'configured': True, 'status': 'online',
             'attached': True, 'serving': True},
            {'uid': 'sim-x', 'configured': False, 'status': 'discovered'},
        ],
    }

    out = _make_disconnected_snapshot(snap)

    assert out['protocol_version'] == PROTOCOL_VERSION
    assert out['session'] is None
    assert out['online_count'] == 0
    assert out['expected_count'] == 1
    assert [d['uid'] for d in out['devices']] == ['sim-a']   # discovered dropped
    dev = out['devices'][0]
    assert dev['status'] == 'offline'
    assert dev['attached'] is False and dev['serving'] is False


def test_empty_snapshot_includes_programs_after_disconnect():
    out = _make_disconnected_snapshot(None)

    assert out['programs'] == []


def test_catalog_message_replaces_snapshot_programs():
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080)
    server._snapshot['programs'] = [{'program_id': 'old'}]

    server._apply_json_message({
        'type': 'catalog',
        'programs': [{'program_id': 'new', 'beat': 0.5, 'duration': 4.0, 'error': None}],
    })

    assert server._snapshot['programs'] == [
        {'program_id': 'new', 'beat': 0.5, 'duration': 4.0, 'error': None},
    ]


def test_state_message_replaces_devices_session_and_sim_map():
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080)

    server._apply_json_message({
        'type': 'state',
        'session': {'state': 'idle', 'session_id': 0},
        'devices': [
            {'uid': 'sim-a', 'configured': True, 'status': 'online',
             'strip_id': 'main', 'length': 30},
            {'uid': 'esp-000000000001', 'configured': True, 'status': 'offline',
             'strip_id': 'side', 'length': 60},
            {'uid': 'sim-new', 'configured': False, 'status': 'discovered'},
        ],
    })

    snap = server._snapshot
    assert [d['uid'] for d in snap['devices']] == [
        'sim-a', 'esp-000000000001', 'sim-new']
    assert snap['session'] == {'state': 'idle', 'session_id': 0}
    assert snap['online_count'] == 1     # only the online configured device
    assert snap['expected_count'] == 2   # both configured devices
    # only configured sim-* devices feed the layout map
    assert server._sim_devices == {'sim-a': 30}


def test_web_ui_server_initial_snapshot_includes_layouts():
    layouts = {'sim-1': {'rows': [[1, 2], [None, 3]]}}
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, layouts)

    assert server._snapshot['layouts'] == layouts


def test_state_message_keeps_layouts_and_server_version():
    layouts = {'sim-1': {'rows': [[1, None], [2, 3]]}}
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, layouts,
                         server_version='v-test')

    server._apply_json_message({
        'type': 'state',
        'session': None,
        'devices': [{'uid': 'sim-1', 'configured': True, 'status': 'online',
                     'strip_id': 'main', 'length': 12}],
    })

    assert server._snapshot['layouts'] == layouts
    assert server._snapshot['server_version'] == 'v-test'


def test_make_disconnected_snapshot_preserves_layouts():
    snap = {
        'type': 'event',
        'event': 'snapshot',
        'protocol_version': PROTOCOL_VERSION,
        'online_count': 1,
        'expected_count': 1,
        'session': {'session_id': 7},
        'devices': [{'uid': 'sim-1', 'configured': True, 'status': 'online'}],
        'programs': [],
        'layouts': {'sim-1': {'rows': [[1], [2]]}},
    }

    out = _make_disconnected_snapshot(snap)

    assert out['layouts'] == {'sim-1': {'rows': [[1], [2]]}}
    assert out['session'] is None
    assert out['devices'][0]['status'] == 'offline'


def test_make_disconnected_snapshot_preserves_server_version():
    snap = {
        'type': 'event',
        'event': 'snapshot',
        'protocol_version': PROTOCOL_VERSION,
        'server_version': 'v-test',
        'online_count': 1,
        'expected_count': 1,
        'session': {'session_id': 7},
        'devices': [{'uid': 'sim-1', 'configured': True, 'status': 'online'}],
        'programs': [],
    }

    out = _make_disconnected_snapshot(snap)

    assert out['server_version'] == 'v-test'


def test_resolve_asset_path_allows_nested_assets(tmp_path, monkeypatch):
    static_dir = tmp_path / 'dist'
    asset_dir = static_dir / 'assets'
    asset_dir.mkdir(parents=True)
    asset_path = asset_dir / 'index-abc123.js'
    asset_path.write_text('console.log("ok")', encoding='utf-8')

    monkeypatch.setattr(web_mod, '_STATIC_DIR', static_dir)
    monkeypatch.setattr(web_mod, '_STATIC_ROOT', static_dir.resolve())

    assert _resolve_asset_path('/assets/index-abc123.js') == asset_path.resolve()


def test_resolve_asset_path_rejects_traversal(tmp_path, monkeypatch):
    static_dir = tmp_path / 'dist'
    static_dir.mkdir()
    (tmp_path / 'secret.txt').write_text('nope', encoding='utf-8')

    monkeypatch.setattr(web_mod, '_STATIC_DIR', static_dir)
    monkeypatch.setattr(web_mod, '_STATIC_ROOT', static_dir.resolve())

    assert _resolve_asset_path('/../secret.txt') is None
    assert _resolve_asset_path('/..%2Fsecret.txt') is None


def test_static_cache_control_is_immutable_for_hashed_assets(tmp_path, monkeypatch):
    static_dir = tmp_path / 'dist'
    asset_dir = static_dir / 'assets'
    asset_dir.mkdir(parents=True)
    asset_path = asset_dir / 'index-abc123.js'
    asset_path.write_text('console.log("ok")', encoding='utf-8')

    monkeypatch.setattr(web_mod, '_STATIC_DIR', static_dir)
    monkeypatch.setattr(web_mod, '_STATIC_ROOT', static_dir.resolve())

    assert _static_cache_control(asset_path.resolve()) == 'public, max-age=31536000, immutable'


def test_static_cache_control_keeps_html_uncached(tmp_path, monkeypatch):
    static_dir = tmp_path / 'dist'
    static_dir.mkdir()
    index_path = static_dir / 'index.html'
    index_path.write_text('<!doctype html>', encoding='utf-8')

    monkeypatch.setattr(web_mod, '_STATIC_DIR', static_dir)
    monkeypatch.setattr(web_mod, '_STATIC_ROOT', static_dir.resolve())

    assert _static_cache_control(index_path.resolve()) == 'no-store'


def test_layout_device_uid_from_path_extracts_device_uid():
    assert _layout_device_uid_from_path('/api/layouts/sim-1') == 'sim-1'
    assert _layout_device_uid_from_path('/api/layouts/') is None
    assert _layout_device_uid_from_path('/api/layouts/a/b') is None


def test_device_uid_from_path_extracts_device_uid():
    assert _device_uid_from_path('/api/devices/sim-1') == 'sim-1'
    assert _device_uid_from_path('/api/devices/') is None
    assert _device_uid_from_path('/api/devices/a/b') is None


def test_tailscale_urls_formats_ipv4_output(monkeypatch):
    class Result:
        returncode = 0
        stdout = "100.101.102.103\n100.64.0.5\n"

    monkeypatch.setattr(web_mod.shutil, 'which', lambda name: '/usr/bin/tailscale')
    monkeypatch.setattr(web_mod.subprocess, 'run', lambda *args, **kwargs: Result())

    assert web_mod._tailscale_urls(8080) == [
        'http://100.101.102.103:8080/',
        'http://100.64.0.5:8080/',
    ]


def test_tailscale_urls_returns_empty_when_binary_missing(monkeypatch):
    monkeypatch.setattr(web_mod.shutil, 'which', lambda name: None)

    assert web_mod._tailscale_urls(8080) == []


def test_get_layout_response_missing_known_sim_returns_404(tmp_path):
    server = WebUiServer(
        '/tmp/elemctl.sock',
        '127.0.0.1',
        8080,
        {},
        {'sim-1': 4},
        str(tmp_path),
    )

    status, payload = server._get_layout_response('sim-1')

    assert status == 404
    assert payload == {'error': 'layout not found'}


def test_get_layout_response_unknown_device_returns_400(tmp_path):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))

    status, payload = server._get_layout_response('sim-1')

    assert status == 400
    assert payload == {'error': 'unknown sim device'}


def test_get_layout_response_returns_rows_without_editor_when_sidecar_missing(tmp_path):
    (tmp_path / 'sim-1.csv').write_text("1,2\n3,4\n", encoding='utf-8')
    server = WebUiServer(
        '/tmp/elemctl.sock',
        '127.0.0.1',
        8080,
        {},
        {'sim-1': 4},
        str(tmp_path),
    )

    status, payload = server._get_layout_response('sim-1')

    assert status == 200
    assert payload == {
        'rows': [
            [1, 2],
            [3, 4],
        ],
        'editor': None,
    }


def test_save_layout_response_updates_layout_cache_and_snapshot(tmp_path):
    server = WebUiServer(
        '/tmp/elemctl.sock',
        '127.0.0.1',
        8080,
        {},
        {'sim-1': 4},
        str(tmp_path),
    )

    status, payload = server._save_layout_response(
        'sim-1',
        {
            'rows': [[1, 2], [3, 4]],
            'editor': {
                'version': 1,
                'primitives': [
                    {'type': 'single', 'index': 1, 'position': [0, 0]},
                    {'type': 'single', 'index': 2, 'position': [1, 0]},
                    {'type': 'single', 'index': 3, 'position': [0, 1]},
                    {'type': 'single', 'index': 4, 'position': [1, 1]},
                ],
            },
            'base_csv_hash': None,
        },
    )

    assert status == 200
    assert payload['rows'] == [
        [1, 2],
        [3, 4],
    ]
    assert payload['editor']['csv_hash']
    assert server._layouts == {'sim-1': {'rows': [[1, 2], [3, 4]]}}
    assert server._snapshot['layouts'] == server._layouts


def test_save_layout_response_rejects_invalid_payload(tmp_path):
    server = WebUiServer(
        '/tmp/elemctl.sock',
        '127.0.0.1',
        8080,
        {},
        {'sim-1': 4},
        str(tmp_path),
    )

    status, payload = server._save_layout_response(
        'sim-1',
        {
            'rows': [[1, 2]],
            'editor': {
                'version': 1,
                'primitives': [
                    {'type': 'single', 'index': 1, 'position': [1, 0]},
                    {'type': 'single', 'index': 2, 'position': [0, 0]},
                ],
            },
            'base_csv_hash': None,
        },
    )

    assert status == 400
    assert payload == {'error': 'editor primitives do not match rows'}


def test_create_device_response_forwards_add_device(monkeypatch, tmp_path):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))
    commands: list[dict] = []

    class FakeClient:
        def __init__(self, socket_path: str, timeout: float, role: str) -> None:
            assert socket_path == '/tmp/elemctl.sock'
            assert role == web_mod.ROLE_WRITER
            self._reads = [
                [
                    (
                        web_mod.KIND_JSON,
                        json.dumps({'type': 'event', 'event': 'snapshot'}).encode('utf-8'),
                    ),
                ],
                [
                    (
                        web_mod.KIND_JSON,
                        json.dumps({
                            'type': 'reply',
                            'id': 1,
                            'ok': True,
                            'result': {'message': 'added device sim-1'},
                        }).encode('utf-8'),
                    ),
                ],
            ]

        def next_id(self) -> int:
            return 1

        def send_cmd(self, cmd: dict) -> None:
            commands.append(cmd)

        def recv_once(self) -> list[tuple[int, bytes]]:
            return self._reads.pop(0)

        def close(self) -> None:
            return None

    monkeypatch.setattr(web_mod, 'ControllerClient', FakeClient)

    status, payload = server._create_device_response({
        'device_uid': 'sim-1',
        'strip_id': 'main',
        'length': 60,
    })

    assert status == 200
    assert payload == {'ok': True, 'result': {'message': 'added device sim-1'}}
    assert commands == [{
        'id': 1,
        'cmd': 'add_device',
        'device_uid': 'sim-1',
        'strip_id': 'main',
        'length': 60,
    }]


def test_create_device_response_rejects_invalid_payload(tmp_path):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))

    status, payload = server._create_device_response({
        'device_uid': 'sim-1',
        'strip_id': '',
        'length': 60,
    })

    assert status == 400
    assert payload == {'error': 'strip_id must be a non-empty string'}


def test_create_device_response_returns_503_when_controller_unavailable(monkeypatch, tmp_path):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))

    class FailingClient:
        def __init__(self, socket_path: str, timeout: float, role: str) -> None:
            raise ConnectionError('hello failed')

    monkeypatch.setattr(web_mod, 'ControllerClient', FailingClient)

    status, payload = server._create_device_response({
        'device_uid': 'sim-1',
        'strip_id': 'main',
        'length': 60,
    })

    assert status == 503
    assert payload == {'error': 'controller unavailable: hello failed'}


def test_create_device_response_returns_503_when_controller_reply_times_out(monkeypatch, tmp_path):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))

    class FakeSock:
        def __init__(self) -> None:
            self.timeout = 1.5

        def gettimeout(self) -> float:
            return self.timeout

        def settimeout(self, timeout: float) -> None:
            self.timeout = timeout

    class TimeoutClient:
        def __init__(self, socket_path: str, timeout: float, role: str) -> None:
            assert socket_path == '/tmp/elemctl.sock'
            assert role == web_mod.ROLE_WRITER
            self._sock = FakeSock()

        def next_id(self) -> int:
            return 1

        def send_cmd(self, cmd: dict) -> None:
            return None

        def recv_once(self) -> list[tuple[int, bytes]]:
            raise TimeoutError('timed out')

        def close(self) -> None:
            return None

    monkeypatch.setattr(web_mod, 'ControllerClient', TimeoutClient)
    monkeypatch.setattr(web_mod, '_CONTROLLER_REPLY_TIMEOUT', 0.01)

    status, payload = server._create_device_response({
        'device_uid': 'sim-1',
        'strip_id': 'main',
        'length': 60,
    })

    assert status == 503
    assert payload == {'error': 'controller timed out'}


def test_edit_device_response_forwards_edit_device(monkeypatch, tmp_path):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))
    commands: list[dict] = []

    class FakeClient:
        def __init__(self, socket_path: str, timeout: float, role: str) -> None:
            assert socket_path == '/tmp/elemctl.sock'
            assert role == web_mod.ROLE_WRITER
            self._reads = [[
                (
                    web_mod.KIND_JSON,
                    json.dumps({
                        'type': 'reply',
                        'id': 1,
                        'ok': True,
                        'result': {'message': 'updated device old -> new'},
                    }).encode('utf-8'),
                ),
            ]]

        def next_id(self) -> int:
            return 1

        def send_cmd(self, cmd: dict) -> None:
            commands.append(cmd)

        def recv_once(self) -> list[tuple[int, bytes]]:
            return self._reads.pop(0)

        def close(self) -> None:
            return None

    monkeypatch.setattr(web_mod, 'ControllerClient', FakeClient)

    status, payload = server._edit_device_response('old-uid', {
        'device_uid': 'new-uid',
        'strip_id': 'main',
        'length': 90,
    })

    assert status == 200
    assert payload == {'ok': True, 'result': {'message': 'updated device old -> new'}}
    assert commands == [{
        'id': 1,
        'cmd': 'edit_device',
        'target_device_uid': 'old-uid',
        'device_uid': 'new-uid',
        'strip_id': 'main',
        'length': 90,
    }]


def test_edit_device_response_rejects_invalid_payload(tmp_path):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))

    status, payload = server._edit_device_response('sim-1', {
        'device_uid': 'sim-1',
        'strip_id': '',
        'length': 60,
    })

    assert status == 400
    assert payload == {'error': 'strip_id must be a non-empty string'}


def test_edit_device_response_returns_503_when_controller_unavailable(monkeypatch, tmp_path):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))

    class FailingClient:
        def __init__(self, socket_path: str, timeout: float, role: str) -> None:
            raise ConnectionError('hello failed')

    monkeypatch.setattr(web_mod, 'ControllerClient', FailingClient)

    status, payload = server._edit_device_response('sim-1', {
        'device_uid': 'sim-1',
        'strip_id': 'main',
        'length': 60,
    })

    assert status == 503
    assert payload == {'error': 'controller unavailable: hello failed'}


def test_remove_device_response_forwards_remove_device(monkeypatch, tmp_path):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))
    commands: list[dict] = []

    class FakeClient:
        def __init__(self, socket_path: str, timeout: float, role: str) -> None:
            assert socket_path == '/tmp/elemctl.sock'
            assert role == web_mod.ROLE_WRITER
            self._reads = [[
                (
                    web_mod.KIND_JSON,
                    json.dumps({
                        'type': 'reply',
                        'id': 1,
                        'ok': True,
                        'result': {'message': 'removed device sim-1'},
                    }).encode('utf-8'),
                ),
            ]]

        def next_id(self) -> int:
            return 1

        def send_cmd(self, cmd: dict) -> None:
            commands.append(cmd)

        def recv_once(self) -> list[tuple[int, bytes]]:
            return self._reads.pop(0)

        def close(self) -> None:
            return None

    monkeypatch.setattr(web_mod, 'ControllerClient', FakeClient)

    status, payload = server._remove_device_response('sim-1')

    assert status == 200
    assert payload == {'ok': True, 'result': {'message': 'removed device sim-1'}}
    assert commands == [{
        'id': 1,
        'cmd': 'remove_device',
        'device_uid': 'sim-1',
    }]


def test_remove_device_response_rejects_invalid_uid(tmp_path):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))

    status, payload = server._remove_device_response('')

    assert status == 400
    assert payload == {'error': 'device uid must be a non-empty string'}


def test_remove_device_response_returns_503_when_controller_unavailable(monkeypatch, tmp_path):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))

    class FailingClient:
        def __init__(self, socket_path: str, timeout: float, role: str) -> None:
            raise ConnectionError('hello failed')

    monkeypatch.setattr(web_mod, 'ControllerClient', FailingClient)

    status, payload = server._remove_device_response('sim-1')

    assert status == 503
    assert payload == {'error': 'controller unavailable: hello failed'}


def test_read_http_body_rejects_oversized_request(tmp_path):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))
    
    async def run() -> None:
        reader = asyncio.StreamReader()
        reader.feed_eof()
        await server._read_http_body(reader, {'content-length': str((1 << 20) + 1)})

    with pytest.raises(web_mod._HttpError) as exc:
        asyncio.run(run())

    assert exc.value.status == 413
    assert exc.value.message == 'request body too large'


def test_close_all_ws_clients_aborts_stuck_writer(tmp_path, monkeypatch):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))

    class Transport:
        def __init__(self) -> None:
            self.aborted = False

        def abort(self) -> None:
            self.aborted = True

    class Writer:
        def __init__(self) -> None:
            self.closed = False
            self.transport = Transport()

        def close(self) -> None:
            self.closed = True

        async def wait_closed(self) -> None:
            await asyncio.Event().wait()

    writer = Writer()
    server._ws_clients.add(web_mod._WsClient(writer=writer, peer='browser'))  # type: ignore[arg-type]
    monkeypatch.setattr(web_mod, '_WS_CLOSE_TIMEOUT', 0.001)

    asyncio.run(server._close_all_ws_clients())

    assert writer.closed is True
    assert writer.transport.aborted is True
    assert server._ws_clients == set()


def test_write_http_response_ignores_connection_reset_on_close(tmp_path):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))

    class Writer:
        def __init__(self) -> None:
            self.buffer = bytearray()
            self.closed = False

        def write(self, data: bytes) -> None:
            self.buffer.extend(data)

        async def drain(self) -> None:
            return None

        def close(self) -> None:
            self.closed = True

        async def wait_closed(self) -> None:
            raise ConnectionResetError(104, 'Connection reset by peer')

    writer = Writer()

    asyncio.run(server._write_http_response(writer, 200, b'ok'))  # type: ignore[arg-type]

    assert writer.closed is True
    assert bytes(writer.buffer).startswith(b'HTTP/1.1 200 OK\r\n')


def test_handle_http_client_ignores_connection_reset_from_ws_handler(tmp_path):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))

    async def fail_ws(reader, writer, headers) -> None:
        raise ConnectionResetError(104, 'Connection reset by peer')

    server._handle_ws = fail_ws  # type: ignore[method-assign]

    async def run() -> None:
        reader = asyncio.StreamReader()
        reader.feed_data(
            (
                b'GET /ws HTTP/1.1\r\n'
                b'Host: localhost\r\n'
                b'Upgrade: websocket\r\n'
                b'Connection: Upgrade\r\n'
                b'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n'
                b'\r\n'
            )
        )
        reader.feed_eof()

        class Writer:
            def close(self) -> None:
                return None

            async def wait_closed(self) -> None:
                return None

        await server._handle_http_client(reader, Writer())  # type: ignore[arg-type]

    asyncio.run(run())


def test_shutdown_cancels_ws_tasks_before_waiting_for_server_close(tmp_path):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))
    events: list[str] = []

    class Loop:
        def remove_signal_handler(self, signum) -> None:
            events.append(f'remove-handler:{int(signum)}')

    class Server:
        def close(self) -> None:
            events.append('server-close')

        async def wait_closed(self) -> None:
            events.append('server-wait-closed')

    class Thread:
        def join(self, timeout=None) -> None:
            events.append(f'thread-join:{timeout}')

    async def fake_close_all_ws_clients() -> None:
        events.append('close-ws-clients')

    async def ws_task_body() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            events.append('ws-task-cancelled')
            raise

    async def broadcast_task_body() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            events.append('broadcast-task-cancelled')
            raise

    async def run() -> None:
        server._loop = Loop()  # type: ignore[assignment]
        server._controller_thread = Thread()  # type: ignore[assignment]
        server._close_all_ws_clients = fake_close_all_ws_clients  # type: ignore[method-assign]

        ws_task = asyncio.create_task(ws_task_body())
        await asyncio.sleep(0)
        server._ws_tasks.add(ws_task)

        broadcast_task = asyncio.create_task(broadcast_task_body())
        await asyncio.sleep(0)

        await server._shutdown(Server(), broadcast_task, [signal.SIGINT])

    asyncio.run(run())

    assert server._stop.is_set() is True
    assert events.index('server-close') < events.index('ws-task-cancelled')
    assert events.index('ws-task-cancelled') < events.index('server-wait-closed')
    assert events.index('broadcast-task-cancelled') < events.index('server-wait-closed')
    assert events[-1] == 'close-ws-clients'


def test_controller_reader_loop_logs_controller_connect_and_disconnect(monkeypatch, tmp_path, caplog):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))

    class FakeClient:
        def __init__(self) -> None:
            self.recv_calls = 0

        def fileno(self) -> int:
            return 123

        def recv_once(self) -> list[tuple[int, bytes]]:
            self.recv_calls += 1
            if self.recv_calls == 1:
                return []
            raise ConnectionError('server closed connection')

        def close(self) -> None:
            return None

    created = {'count': 0}

    def fake_client(socket_path: str, timeout: float, role: str):
        created['count'] += 1
        if created['count'] == 1:
            return FakeClient()
        server._stop.set()
        raise ConnectionError('controller unavailable')

    monkeypatch.setattr(web_mod, 'ControllerClient', fake_client)
    monkeypatch.setattr(web_mod.select, 'select', lambda *args, **kwargs: ([123], [], []))

    with caplog.at_level(logging.INFO, logger='elemctl.web'):
        server._controller_reader_loop()

    messages = [record.getMessage() for record in caplog.records]
    assert 'controller connected: /tmp/elemctl.sock' in messages
    assert 'controller disconnected: /tmp/elemctl.sock' in messages


def test_web_parser_defaults_bind_all_interfaces():
    parser = _build_parser()
    args = parser.parse_args([])

    assert args.host == '0.0.0.0'
    assert args.port == 8080


# --- round 2: program / session playback endpoints ---------------------------


def _capturing_client(commands: list, reply: dict):
    """A ControllerClient stand-in that records sent commands and replies with
    a snapshot read followed by `reply` (mirrors the device-endpoint fakes)."""

    class FakeClient:
        def __init__(self, socket_path: str, timeout: float, role: str) -> None:
            assert socket_path == '/tmp/elemctl.sock'
            assert role == web_mod.ROLE_WRITER
            self._reads = [
                [(web_mod.KIND_JSON,
                  json.dumps({'type': 'event', 'event': 'snapshot'}).encode('utf-8'))],
                [(web_mod.KIND_JSON, json.dumps(reply).encode('utf-8'))],
            ]

        def next_id(self) -> int:
            return 1

        def send_cmd(self, cmd: dict) -> None:
            commands.append(cmd)

        def recv_once(self):
            return self._reads.pop(0)

        def close(self) -> None:
            return None

    return FakeClient


_OK_REPLY = {'type': 'reply', 'id': 1, 'ok': True, 'result': {}}


class _FakeHttpWriter:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


def _run_http_request(server, method: str, path: str) -> tuple[int, dict]:
    async def run() -> tuple[int, dict]:
        reader = asyncio.StreamReader()
        reader.feed_data(f'{method} {path} HTTP/1.1\r\nHost: x\r\n\r\n'.encode('ascii'))
        reader.feed_eof()
        writer = _FakeHttpWriter()
        await server._handle_http_client(reader, writer)  # type: ignore[arg-type]
        head, _, body = bytes(writer.buffer).partition(b'\r\n\r\n')
        status = int(head.split(b'\r\n')[0].split()[1])
        try:
            return status, json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return status, {}   # non-JSON body (e.g. plain-text 405)

    return asyncio.run(run())


def test_program_id_from_load_path_extracts_id():
    assert _program_id_from_load_path('/api/programs/ring16_blue_wave/load') == 'ring16_blue_wave'
    assert _program_id_from_load_path('/api/programs/rescan') is None
    assert _program_id_from_load_path('/api/programs//load') is None
    assert _program_id_from_load_path('/api/programs/a/b/load') is None


def test_session_verb_from_path_accepts_only_known_verbs():
    for verb in ('play', 'pause', 'resume', 'stop'):
        assert _session_verb_from_path(f'/api/session/{verb}') == verb
    assert _session_verb_from_path('/api/session/loop') is None
    assert _session_verb_from_path('/api/session/') is None


def test_load_program_response_forwards_load(monkeypatch, tmp_path):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))
    commands: list[dict] = []
    monkeypatch.setattr(web_mod, 'ControllerClient', _capturing_client(commands, _OK_REPLY))

    status, payload = server._load_program_response('ring16_blue_wave')

    assert status == 200
    assert payload == {'ok': True, 'result': {}}
    assert commands == [{'id': 1, 'cmd': 'load', 'program_id': 'ring16_blue_wave'}]


def test_load_program_response_rejects_empty_program_id(tmp_path):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))
    status, payload = server._load_program_response('')
    assert status == 400
    assert payload == {'error': 'program_id must be a non-empty string'}


def test_controller_command_response_forwards_rescan(monkeypatch, tmp_path):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))
    commands: list[dict] = []
    monkeypatch.setattr(web_mod, 'ControllerClient', _capturing_client(commands, _OK_REPLY))

    status, payload = server._controller_command_response({'cmd': 'rescan'})

    assert status == 200
    assert payload == {'ok': True, 'result': {}}
    assert commands == [{'id': 1, 'cmd': 'rescan'}]


def test_controller_command_response_maps_controller_error(monkeypatch, tmp_path):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))
    reply = {'type': 'reply', 'id': 1, 'ok': False, 'error': 'unknown program: x'}
    monkeypatch.setattr(web_mod, 'ControllerClient', _capturing_client([], reply))

    status, payload = server._controller_command_response({'cmd': 'load', 'program_id': 'x'})

    assert status == 400
    assert payload == {'error': 'unknown program: x'}


def test_controller_command_response_returns_503_when_unavailable(monkeypatch, tmp_path):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))

    class FailingClient:
        def __init__(self, socket_path: str, timeout: float, role: str) -> None:
            raise ConnectionError('hello failed')

    monkeypatch.setattr(web_mod, 'ControllerClient', FailingClient)

    status, payload = server._controller_command_response({'cmd': 'play'})

    assert status == 503
    assert payload == {'error': 'controller unavailable: hello failed'}


def test_route_load_forwards_to_controller(monkeypatch, tmp_path):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))
    commands: list[dict] = []
    monkeypatch.setattr(web_mod, 'ControllerClient', _capturing_client(commands, _OK_REPLY))

    status, payload = _run_http_request(server, 'POST', '/api/programs/ring16_blue_wave/load')

    assert status == 200
    assert payload == {'ok': True, 'result': {}}
    assert commands == [{'id': 1, 'cmd': 'load', 'program_id': 'ring16_blue_wave'}]


def test_route_session_play_forwards_to_controller(monkeypatch, tmp_path):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))
    commands: list[dict] = []
    monkeypatch.setattr(web_mod, 'ControllerClient', _capturing_client(commands, _OK_REPLY))

    status, payload = _run_http_request(server, 'POST', '/api/session/play')

    assert status == 200
    assert payload == {'ok': True, 'result': {}}
    assert commands == [{'id': 1, 'cmd': 'play'}]


def test_route_session_rejects_get(tmp_path):
    server = WebUiServer('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))
    status, _ = _run_http_request(server, 'GET', '/api/session/play')
    assert status == 405
