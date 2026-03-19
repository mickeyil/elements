import asyncio
import json
import signal
import elemctl.web as web_mod
import pytest
from elemctl.uds_wire import PROTOCOL_VERSION
from elemctl.web import (
    WebRelay,
    _build_parser,
    _encode_ws_frame,
    _layout_device_uid_from_path,
    _make_disconnected_snapshot,
    _resolve_asset_path,
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


def test_make_disconnected_snapshot_clears_session_and_devices():
    snap = {
        'type': 'event',
        'event': 'snapshot',
        'protocol_version': PROTOCOL_VERSION,
        'online_count': 2,
        'expected_count': 2,
        'session': {'session_id': 5},
        'devices': [
            {'device_id': 1, 'connected': True},
            {'device_id': 2, 'connected': True},
        ],
    }

    out = _make_disconnected_snapshot(snap)

    assert out['protocol_version'] == PROTOCOL_VERSION
    assert out['session'] is None
    assert out['online_count'] == 0
    assert out['expected_count'] == 2
    assert [dev['connected'] for dev in out['devices']] == [False, False]


def test_empty_snapshot_includes_programs_after_disconnect():
    out = _make_disconnected_snapshot(None)

    assert out['programs'] == []


def test_programs_updated_refreshes_cached_snapshot_programs():
    relay = WebRelay('/tmp/elemctl.sock', '127.0.0.1', 8080)
    relay._snapshot = {
        'type': 'event',
        'event': 'snapshot',
        'protocol_version': PROTOCOL_VERSION,
        'online_count': 0,
        'expected_count': 0,
        'session': None,
        'devices': [],
        'programs': [{'program_id': 'old', 'beat': 1.0, 'duration': 2.0, 'error': None}],
    }

    relay._apply_json_message({
        'type': 'event',
        'event': 'programs_updated',
        'programs': [{'program_id': 'new', 'beat': 0.5, 'duration': 4.0, 'error': None}],
    })

    assert relay._snapshot['programs'] == [
        {'program_id': 'new', 'beat': 0.5, 'duration': 4.0, 'error': None},
    ]


def test_web_relay_initial_snapshot_includes_layouts():
    layouts = {'sim-1': {'rows': [[1, 2], [None, 3]]}}
    relay = WebRelay('/tmp/elemctl.sock', '127.0.0.1', 8080, layouts)

    assert relay._snapshot['layouts'] == layouts


def test_snapshot_event_reapplies_layouts():
    layouts = {'sim-1': {'rows': [[1, None], [2, 3]]}}
    relay = WebRelay('/tmp/elemctl.sock', '127.0.0.1', 8080, layouts)

    relay._apply_json_message({
        'type': 'event',
        'event': 'snapshot',
        'protocol_version': PROTOCOL_VERSION,
        'online_count': 1,
        'expected_count': 1,
        'session': None,
        'devices': [{'device_id': 1, 'connected': True}],
        'programs': [],
    })

    assert relay._snapshot['layouts'] == layouts


def test_make_disconnected_snapshot_preserves_layouts():
    snap = {
        'type': 'event',
        'event': 'snapshot',
        'protocol_version': PROTOCOL_VERSION,
        'online_count': 1,
        'expected_count': 1,
        'session': {'session_id': 7},
        'devices': [{'device_id': 1, 'connected': True}],
        'programs': [],
        'layouts': {'sim-1': {'rows': [[1], [2]]}},
    }

    out = _make_disconnected_snapshot(snap)

    assert out['layouts'] == {'sim-1': {'rows': [[1], [2]]}}
    assert out['session'] is None
    assert out['devices'][0]['connected'] is False


def test_resolve_asset_path_allows_nested_assets(tmp_path, monkeypatch):
    static_dir = tmp_path / 'web_static'
    asset_dir = static_dir / 'assets'
    asset_dir.mkdir(parents=True)
    asset_path = asset_dir / 'index-abc123.js'
    asset_path.write_text('console.log("ok")', encoding='utf-8')

    monkeypatch.setattr(web_mod, '_STATIC_DIR', static_dir)
    monkeypatch.setattr(web_mod, '_STATIC_ROOT', static_dir.resolve())

    assert _resolve_asset_path('/assets/index-abc123.js') == asset_path.resolve()


def test_resolve_asset_path_rejects_traversal(tmp_path, monkeypatch):
    static_dir = tmp_path / 'web_static'
    static_dir.mkdir()
    (tmp_path / 'secret.txt').write_text('nope', encoding='utf-8')

    monkeypatch.setattr(web_mod, '_STATIC_DIR', static_dir)
    monkeypatch.setattr(web_mod, '_STATIC_ROOT', static_dir.resolve())

    assert _resolve_asset_path('/../secret.txt') is None
    assert _resolve_asset_path('/..%2Fsecret.txt') is None


def test_layout_device_uid_from_path_extracts_device_uid():
    assert _layout_device_uid_from_path('/api/layouts/sim-1') == 'sim-1'
    assert _layout_device_uid_from_path('/api/layouts/') is None
    assert _layout_device_uid_from_path('/api/layouts/a/b') is None


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
    relay = WebRelay(
        '/tmp/elemctl.sock',
        '127.0.0.1',
        8080,
        {},
        {'sim-1': 4},
        str(tmp_path),
    )

    status, payload = relay._get_layout_response('sim-1')

    assert status == 404
    assert payload == {'error': 'layout not found'}


def test_get_layout_response_unknown_device_returns_400(tmp_path):
    relay = WebRelay('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))

    status, payload = relay._get_layout_response('sim-1')

    assert status == 400
    assert payload == {'error': 'unknown sim device'}


def test_get_layout_response_returns_rows_without_editor_when_sidecar_missing(tmp_path):
    (tmp_path / 'sim-1.csv').write_text("1,2\n3,4\n", encoding='utf-8')
    relay = WebRelay(
        '/tmp/elemctl.sock',
        '127.0.0.1',
        8080,
        {},
        {'sim-1': 4},
        str(tmp_path),
    )

    status, payload = relay._get_layout_response('sim-1')

    assert status == 200
    assert payload == {
        'rows': [
            [1, 2],
            [3, 4],
        ],
        'editor': None,
    }


def test_save_layout_response_updates_layout_cache_and_snapshot(tmp_path):
    relay = WebRelay(
        '/tmp/elemctl.sock',
        '127.0.0.1',
        8080,
        {},
        {'sim-1': 4},
        str(tmp_path),
    )

    status, payload = relay._save_layout_response(
        'sim-1',
        {
            'rows': [[None, 1, 2], [None, 3, 4]],
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
    assert relay._layouts == {'sim-1': {'rows': [[1, 2], [3, 4]]}}
    assert relay._snapshot['layouts'] == relay._layouts


def test_save_layout_response_rejects_invalid_payload(tmp_path):
    relay = WebRelay(
        '/tmp/elemctl.sock',
        '127.0.0.1',
        8080,
        {},
        {'sim-1': 4},
        str(tmp_path),
    )

    status, payload = relay._save_layout_response(
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
    relay = WebRelay('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))
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

    monkeypatch.setattr(web_mod, 'UdsClient', FakeClient)

    status, payload = relay._create_device_response({
        'device_type': 'sim',
        'device_uid': 'sim-1',
        'strip_id': 'main',
        'length': 60,
    })

    assert status == 200
    assert payload == {'ok': True, 'result': {'message': 'added device sim-1'}}
    assert commands == [{
        'id': 1,
        'cmd': 'add_device',
        'device_type': 'sim',
        'device_uid': 'sim-1',
        'strip_id': 'main',
        'length': 60,
    }]


def test_create_device_response_rejects_invalid_payload(tmp_path):
    relay = WebRelay('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))

    status, payload = relay._create_device_response({
        'device_type': 'sim',
        'device_uid': 'sim-1',
        'strip_id': '',
        'length': 60,
    })

    assert status == 400
    assert payload == {'error': 'strip_id must be a non-empty string'}


def test_create_device_response_returns_503_when_controller_unavailable(monkeypatch, tmp_path):
    relay = WebRelay('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))

    class FailingClient:
        def __init__(self, socket_path: str, timeout: float, role: str) -> None:
            raise ConnectionError('hello failed')

    monkeypatch.setattr(web_mod, 'UdsClient', FailingClient)

    status, payload = relay._create_device_response({
        'device_type': 'sim',
        'device_uid': 'sim-1',
        'strip_id': 'main',
        'length': 60,
    })

    assert status == 503
    assert payload == {'error': 'controller unavailable: hello failed'}


def test_read_http_body_rejects_oversized_request(tmp_path):
    relay = WebRelay('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))
    
    async def run() -> None:
        reader = asyncio.StreamReader()
        reader.feed_eof()
        await relay._read_http_body(reader, {'content-length': str((1 << 20) + 1)})

    with pytest.raises(web_mod._HttpError) as exc:
        asyncio.run(run())

    assert exc.value.status == 413
    assert exc.value.message == 'request body too large'


def test_close_all_ws_clients_aborts_stuck_writer(tmp_path, monkeypatch):
    relay = WebRelay('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))

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
    relay._ws_clients.add(web_mod._WsClient(writer=writer, peer='browser'))  # type: ignore[arg-type]
    monkeypatch.setattr(web_mod, '_WS_CLOSE_TIMEOUT', 0.001)

    asyncio.run(relay._close_all_ws_clients())

    assert writer.closed is True
    assert writer.transport.aborted is True
    assert relay._ws_clients == set()


def test_write_http_response_ignores_connection_reset_on_close(tmp_path):
    relay = WebRelay('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))

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

    asyncio.run(relay._write_http_response(writer, 200, b'ok'))  # type: ignore[arg-type]

    assert writer.closed is True
    assert bytes(writer.buffer).startswith(b'HTTP/1.1 200 OK\r\n')


def test_handle_http_client_ignores_connection_reset_from_ws_handler(tmp_path):
    relay = WebRelay('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))

    async def fail_ws(reader, writer, headers) -> None:
        raise ConnectionResetError(104, 'Connection reset by peer')

    relay._handle_ws = fail_ws  # type: ignore[method-assign]

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

        await relay._handle_http_client(reader, Writer())  # type: ignore[arg-type]

    asyncio.run(run())


def test_shutdown_cancels_ws_tasks_before_waiting_for_server_close(tmp_path):
    relay = WebRelay('/tmp/elemctl.sock', '127.0.0.1', 8080, {}, {}, str(tmp_path))
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
        relay._loop = Loop()  # type: ignore[assignment]
        relay._uds_thread = Thread()  # type: ignore[assignment]
        relay._close_all_ws_clients = fake_close_all_ws_clients  # type: ignore[method-assign]

        ws_task = asyncio.create_task(ws_task_body())
        await asyncio.sleep(0)
        relay._ws_tasks.add(ws_task)

        broadcast_task = asyncio.create_task(broadcast_task_body())
        await asyncio.sleep(0)

        await relay._shutdown(Server(), broadcast_task, [signal.SIGINT])

    asyncio.run(run())

    assert relay._stop.is_set() is True
    assert events.index('server-close') < events.index('ws-task-cancelled')
    assert events.index('ws-task-cancelled') < events.index('server-wait-closed')
    assert events.index('broadcast-task-cancelled') < events.index('server-wait-closed')
    assert events[-1] == 'close-ws-clients'


def test_web_parser_defaults_bind_all_interfaces():
    parser = _build_parser()
    args = parser.parse_args([])

    assert args.host == '0.0.0.0'
    assert args.port == 8080
