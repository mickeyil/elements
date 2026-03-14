from elemctl.uds_wire import PROTOCOL_VERSION
from elemctl.web import WebRelay, _encode_ws_frame, _make_disconnected_snapshot


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
