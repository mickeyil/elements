import asyncio
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


def test_web_parser_defaults_bind_all_interfaces():
    parser = _build_parser()
    args = parser.parse_args([])

    assert args.host == '0.0.0.0'
    assert args.port == 8080
