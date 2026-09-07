"""Byte-level tests for the v3 device wire codecs.

Two kinds of coverage: literal byte vectors (so a wrong offset fails
against bytes a human can check against the C++ source), and a parity
test that parses the constants out of the C++ headers so the two
sides cannot drift silently.
"""

import math
import re
import struct
from pathlib import Path

import pytest

from elemctl import wire
from elemctl.wire import (
    LinkReader,
    WireError,
    encode_erase_animation,
    encode_jump,
    encode_load,
    encode_message,
    encode_offer,
    encode_pause,
    encode_ping,
    encode_play_local_animation,
    encode_query_device_status,
    encode_query_local_animations,
    encode_reboot,
    encode_resume,
    encode_set_animation_order,
    encode_set_profile,
    encode_start,
    encode_stop,
    encode_store_animation,
    encode_sync_pong,
    parse_ack,
    parse_device_status,
    parse_discover,
    parse_frame_preview,
    parse_local_animations,
    parse_register,
    parse_sync_ping,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _uid_slot(uid: str) -> bytes:
    return uid.encode('ascii').ljust(16, b'\x00')


def _name_slot(name: str) -> bytes:
    return name.encode('ascii').ljust(32, b'\x00')


# ---------------------------------------------------------------------------
# C++ constant parity
# ---------------------------------------------------------------------------

_CONSTEXPR_RE = re.compile(
    r'constexpr\s+\w+(?:_t)?\s+(\w+)\s*=\s*([0-9a-fA-Fx\'\s*+]+);'
)


def _cpp_constants(relative_path: str) -> dict[str, int]:
    """Parse `constexpr <type> NAME = <integer expr>;` lines from a C++ file."""
    text = (REPO_ROOT / relative_path).read_text()
    out = {}
    for name, expr in _CONSTEXPR_RE.findall(text):
        expr = expr.replace("'", '').strip()
        if not re.fullmatch(r'[0-9a-fA-Fx\s*+]+', expr):
            continue
        out[name] = eval(expr, {'__builtins__': {}}, {})  # noqa: S307
    return out


def test_link_protocol_constants_match_cpp():
    cpp = _cpp_constants('src/controller/link_protocol.h')
    expected = {
        'PROTOCOL_VERSION': wire.PROTOCOL_VERSION,
        'CMD_REGISTER': wire.CMD_REGISTER,
        'CMD_SET_PROFILE': wire.CMD_SET_PROFILE,
        'CMD_LOAD': wire.CMD_LOAD,
        'CMD_START': wire.CMD_START,
        'CMD_JUMP': wire.CMD_JUMP,
        'CMD_PAUSE': wire.CMD_PAUSE,
        'CMD_RESUME': wire.CMD_RESUME,
        'CMD_STOP': wire.CMD_STOP,
        'CMD_PLAY_LOCAL_ANIMATION': wire.CMD_PLAY_LOCAL_ANIMATION,
        'CMD_STORE_ANIMATION': wire.CMD_STORE_ANIMATION,
        'CMD_ERASE_ANIMATION': wire.CMD_ERASE_ANIMATION,
        'CMD_SET_ANIMATION_ORDER': wire.CMD_SET_ANIMATION_ORDER,
        'CMD_REBOOT': wire.CMD_REBOOT,
        'CMD_QUERY_DEVICE_STATUS': wire.CMD_QUERY_DEVICE_STATUS,
        'CMD_PING': wire.CMD_PING,
        'CMD_QUERY_LOCAL_ANIMATIONS': wire.CMD_QUERY_LOCAL_ANIMATIONS,
        'CMD_ACK': wire.CMD_ACK,
        'ACK_OK': wire.ACK_OK,
        'ACK_ERROR': wire.ACK_ERROR,
        'ACK_WRONG_STATE': wire.ACK_WRONG_STATE,
        'ACK_PROFILE_MISMATCH': wire.ACK_PROFILE_MISMATCH,
        'ACK_BAD_PAYLOAD': wire.ACK_BAD_PAYLOAD,
        'ACK_UNKNOWN_COMMAND': wire.ACK_UNKNOWN_COMMAND,
        'ACK_UNSYNCED': wire.ACK_UNSYNCED,
        'PING_INTERVAL_MS': wire.PING_INTERVAL_MS,
        'MAX_BLOB_BYTES': wire.MAX_BLOB_BYTES,
    }
    for name, value in expected.items():
        assert cpp[name] == value, f'{name}: cpp={cpp[name]} python={value}'
    # TCP_MSG_MAX is an expression in the header (MAX_BLOB_BYTES + 256),
    # which the constant parser skips; check the same formula instead.
    assert wire.TCP_MSG_MAX == cpp['MAX_BLOB_BYTES'] + 256


def test_identity_and_store_sizes_match_cpp():
    assert _cpp_constants('src/platform/device_identity.h')['UID_SIZE'] == wire.UID_SIZE
    assert _cpp_constants('src/controller/animation_store.h')['ANIM_NAME_SIZE'] == wire.ANIM_NAME_SIZE


def test_discovery_constants_match_cpp():
    cpp = _cpp_constants('src/controller/discovery.cpp')
    assert cpp['MAGIC'] == wire.DISCOVERY_MAGIC
    assert cpp['PKT_DISCOVER'] == wire.PKT_DISCOVER
    assert cpp['PKT_OFFER'] == wire.PKT_OFFER


def test_sync_constants_match_cpp():
    cpp = _cpp_constants('src/controller/clock_sync_client.cpp')
    assert cpp['PKT_PING'] == wire.SYNC_PKT_PING
    assert cpp['PKT_PONG'] == wire.SYNC_PKT_PONG
    assert cpp['PING_WIRE_SIZE'] == wire.SYNC_PING_WIRE_SIZE
    assert cpp['PONG_WIRE_SIZE'] == wire.SYNC_PONG_WIRE_SIZE


def test_log_constants_match_cpp():
    from elemctl.config import DEFAULT_LOG_PORT
    cpp = _cpp_constants('src/controller/log_sender.cpp')
    assert cpp['LOG_MAGIC'] == wire.LOG_MAGIC
    assert cpp['LOG_VERSION'] == wire.LOG_VERSION
    # The device's port is compile-time; the config default must match
    # it or devices log into the void.
    assert cpp['LOG_PORT'] == DEFAULT_LOG_PORT
    # LOG_HEADER_BYTES is an expression in the C++ (uses UID_SIZE),
    # which the constant parser skips; check the same formula instead.
    assert wire.LOG_HEADER_BYTES == 2 + 1 + wire.UID_SIZE + 4 + 4 + 4 + 1


# ---------------------------------------------------------------------------
# TCP framing
# ---------------------------------------------------------------------------

def test_encode_message_frames_opcode_and_payload():
    assert encode_message(0x41) == b'\x01\x00\x00\x00\x41'
    assert encode_message(0x10, b'\xaa\xbb') == b'\x03\x00\x00\x00\x10\xaa\xbb'


def test_link_reader_reassembles_across_partial_feeds():
    reader = LinkReader()
    data = encode_set_profile(144) + encode_ping()
    for i in range(len(data)):
        reader.feed(data[i:i + 1])
    collected = []
    reader2 = LinkReader()
    reader2.feed(data[:3])
    assert reader2.messages() == []
    reader2.feed(data[3:])
    collected = reader2.messages()
    assert collected == [
        (wire.CMD_SET_PROFILE, b'\x90\x00'),
        (wire.CMD_PING, b''),
    ]


def test_link_reader_rejects_impossible_lengths():
    reader = LinkReader()
    reader.feed(b'\x00\x00\x00\x00')
    with pytest.raises(WireError):
        reader.messages()

    reader = LinkReader()
    reader.feed(struct.pack('<I', wire.TCP_MSG_MAX + 1))
    with pytest.raises(WireError):
        reader.messages()


# ---------------------------------------------------------------------------
# Command encoders
# ---------------------------------------------------------------------------

def test_encode_set_profile_vector():
    assert encode_set_profile(300) == b'\x03\x00\x00\x00\x01\x2c\x01'


def test_encode_load_wraps_blob():
    blob = b'ELEM\x03rest'
    assert encode_load(blob) == struct.pack('<IB', 1 + len(blob), 0x10) + blob


def test_encode_load_rejects_empty_and_oversize():
    with pytest.raises(WireError):
        encode_load(b'')
    with pytest.raises(WireError):
        encode_load(b'x' * (wire.MAX_BLOB_BYTES + 1))


def test_encode_start_and_resume_vectors():
    t = 1_700_000_123_456_789
    expected_payload = struct.pack('<q', t)
    assert encode_start(t) == b'\x09\x00\x00\x00\x11' + expected_payload
    assert encode_resume(t) == b'\x09\x00\x00\x00\x14' + expected_payload


def test_encode_jump_vector_and_finite_check():
    assert encode_jump(1.5) == b'\x05\x00\x00\x00\x12' + struct.pack('<f', 1.5)
    with pytest.raises(WireError):
        encode_jump(math.nan)
    with pytest.raises(WireError):
        encode_jump(math.inf)


def test_encode_jump_normalizes_to_the_ms_grid():
    # The device rounds the float32 to the nearest ms. Raw 0.1255 arrives as
    # 0.12549999 and lands on 125; the compiler places that instant at 126.
    f = struct.unpack('<f', encode_jump(0.1255)[5:])[0]
    assert math.floor(f * 1000 + 0.5) == 126


def test_empty_payload_commands():
    assert encode_pause() == b'\x01\x00\x00\x00\x13'
    assert encode_stop() == b'\x01\x00\x00\x00\x15'
    assert encode_reboot() == b'\x01\x00\x00\x00\x30'
    assert encode_query_device_status() == b'\x01\x00\x00\x00\x40'
    assert encode_ping() == b'\x01\x00\x00\x00\x41'
    assert encode_query_local_animations() == b'\x01\x00\x00\x00\x42'


def test_encode_play_local_animation_vector():
    assert encode_play_local_animation(2) == b'\x03\x00\x00\x00\x16\x02\x00'


def test_encode_store_animation_pads_name_slot():
    msg = encode_store_animation('pulse', b'BLOB')
    assert msg == (struct.pack('<IB', 1 + 32 + 4, 0x20)
                   + _name_slot('pulse') + b'BLOB')


def test_encode_erase_animation_vector():
    assert encode_erase_animation('pulse') == (
        struct.pack('<IB', 33, 0x21) + _name_slot('pulse'))


def test_encode_set_animation_order_vector():
    msg = encode_set_animation_order(['a', 'b'])
    assert msg == (struct.pack('<IB', 1 + 2 + 64, 0x22)
                   + b'\x02\x00' + _name_slot('a') + _name_slot('b'))


def test_name_slot_validation():
    with pytest.raises(WireError):
        encode_erase_animation('')
    with pytest.raises(WireError):
        encode_erase_animation('x' * 33)
    with pytest.raises(WireError):
        encode_erase_animation('café')
    # Exactly ANIM_NAME_SIZE characters fill the slot with no padding.
    full = 'n' * 32
    assert _name_slot(full) in encode_erase_animation(full)


# ---------------------------------------------------------------------------
# Inbound TCP parsers
# ---------------------------------------------------------------------------

def test_parse_register_round_trip():
    payload = _uid_slot('sim-alpha') + struct.pack('<IB', 0xDEADBEEF, 3)
    msg = parse_register(payload)
    assert msg.uid == 'sim-alpha'
    assert msg.boot_token == 0xDEADBEEF
    assert msg.protocol_version == 3


def test_parse_register_full_width_uid():
    uid = 'esp-aabbccddeeff'  # exactly 16 chars, no NUL on the wire
    msg = parse_register(uid.encode() + struct.pack('<IB', 1, 3))
    assert msg.uid == uid


def test_parse_register_rejects_bad_payloads():
    with pytest.raises(WireError):
        parse_register(b'short')
    with pytest.raises(WireError):
        parse_register(b'\x00' * 21)  # empty uid slot


def test_parse_ack():
    ack = parse_ack(b'\x06')
    assert ack.status == wire.ACK_UNSYNCED
    assert ack.payload == b''
    ack = parse_ack(b'\x00payload')
    assert ack.status == wire.ACK_OK
    assert ack.payload == b'payload'
    with pytest.raises(WireError):
        parse_ack(b'')


def test_parse_device_status():
    report = parse_device_status(struct.pack('<BBH', 0, 0x01, 7))
    assert report.mode == 0
    assert wire.DEVICE_MODE_NAMES[report.mode] == 'attached_controlled'
    assert report.profile_present is True
    assert report.animation_count == 7
    with pytest.raises(WireError):
        parse_device_status(b'\x00\x00')


def test_parse_local_animations():
    payload = (b'\x02\x00'
               + _name_slot('a') + struct.pack('<HI', 30, 0x11223344)
               + _name_slot('b') + struct.pack('<HI', 144, 5))
    records = parse_local_animations(payload)
    assert [(r.name, r.strip_length, r.crc32) for r in records] == [
        ('a', 30, 0x11223344),
        ('b', 144, 5),
    ]
    assert parse_local_animations(b'\x00\x00') == []
    with pytest.raises(WireError):
        parse_local_animations(b'\x01\x00' + b'\x00' * 10)  # truncated record
    with pytest.raises(WireError):
        parse_local_animations(b'')


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

def test_parse_discover_vector():
    datagram = b'\xcc\xd1\x01' + _uid_slot('sim-a')
    assert parse_discover(datagram) == 'sim-a'


def test_parse_discover_rejects_noise():
    assert parse_discover(b'') is None
    assert parse_discover(b'\xcc\xd1\x01') is None                      # short
    assert parse_discover(b'\xcc\xd1\x02' + _uid_slot('sim-a')) is None  # OFFER type
    assert parse_discover(b'\xff\xff\x01' + _uid_slot('sim-a')) is None  # bad magic
    assert parse_discover(b'\xcc\xd1\x01' + b'\x00' * 16) is None        # empty uid


def test_encode_offer_vector():
    pkt = encode_offer('192.168.1.7', 6041)
    assert pkt == b'\xcc\xd1\x02' + bytes([192, 168, 1, 7]) + struct.pack('<H', 6041)
    assert len(pkt) == wire.OFFER_WIRE_SIZE


def test_encode_offer_validation():
    with pytest.raises(WireError):
        encode_offer('not-an-ip', 6041)
    with pytest.raises(WireError):
        encode_offer('127.0.0.1', 0)


# ---------------------------------------------------------------------------
# Clock sync
# ---------------------------------------------------------------------------

def test_parse_sync_ping_vector():
    datagram = (b'\x01' + _uid_slot('sim-a')
                + struct.pack('<IIq', 42, 7, -123456789))
    ping = parse_sync_ping(datagram)
    assert ping == wire.SyncPing(uid='sim-a', device_boot_token=42,
                                 seq=7, t1_us=-123456789)
    assert len(datagram) == wire.SYNC_PING_WIRE_SIZE


def test_parse_sync_ping_rejects_noise():
    good = b'\x01' + _uid_slot('sim-a') + struct.pack('<IIq', 1, 1, 1)
    assert parse_sync_ping(good[:-1]) is None
    assert parse_sync_ping(b'\x02' + good[1:]) is None  # PONG type
    assert parse_sync_ping(b'\x01' + b'\x00' * 32) is None  # empty uid


def test_encode_sync_pong_echoes_fields():
    pkt = encode_sync_pong(99, 7, 11, 22, 33)
    assert pkt == b'\x02' + struct.pack('<IIqqq', 99, 7, 11, 22, 33)
    assert len(pkt) == wire.SYNC_PONG_WIRE_SIZE


def test_encode_sync_pong_rejects_zero_token():
    with pytest.raises(WireError):
        encode_sync_pong(0, 1, 1, 2, 3)


# ---------------------------------------------------------------------------
# Frame previews
# ---------------------------------------------------------------------------

def test_parse_frame_preview():
    rgb = bytes(range(9))
    datagram = _uid_slot('sim-a') + struct.pack('<If', 5, 0.25) + rgb
    frame = parse_frame_preview(datagram)
    assert frame.uid == 'sim-a'
    assert frame.frame_index == 5
    assert frame.t_program == 0.25
    assert frame.rgb == rgb


def test_parse_frame_preview_rejects_noise():
    header = _uid_slot('sim-a') + struct.pack('<If', 5, 0.25)
    assert parse_frame_preview(header) is None               # no pixels
    assert parse_frame_preview(header + b'\x00\x01') is None  # not divisible by 3
    assert parse_frame_preview(header[:-1]) is None          # short header


def _log_datagram(uid='sim-a', magic=wire.LOG_MAGIC, version=wire.LOG_VERSION,
                  boot_token=7, seq=3, uptime_ms=1500, level=b'W',
                  text=b'wifi flaky'):
    return (struct.pack('<HB', magic, version) + _uid_slot(uid)
            + struct.pack('<III', boot_token, seq, uptime_ms) + level + text)


def test_parse_log_record():
    record = wire.parse_log_record(_log_datagram())
    assert record.uid == 'sim-a'
    assert record.boot_token == 7
    assert record.seq == 3
    assert record.uptime_ms == 1500
    assert record.level == 'W'
    assert record.text == 'wifi flaky'


def test_parse_log_record_empty_text():
    assert wire.parse_log_record(_log_datagram(text=b'')).text == ''


def test_parse_log_record_defangs_control_characters():
    record = wire.parse_log_record(
        _log_datagram(text=b'line1\nline2 \x1b[31mred\x1b[0m'))
    assert record.text == 'line1�line2 �[31mred�[0m'


def test_parse_log_record_clamps_text_to_device_cap():
    record = wire.parse_log_record(_log_datagram(text=b'x' * 1000))
    assert record.text == 'x' * wire.LOG_TEXT_CAP


def test_parse_log_record_rejects_noise():
    short = _log_datagram(text=b'')[:wire.LOG_HEADER_BYTES - 1]
    assert wire.parse_log_record(short) is None
    assert wire.parse_log_record(_log_datagram(magic=0xBEEF)) is None
    assert wire.parse_log_record(_log_datagram(version=2)) is None
    assert wire.parse_log_record(_log_datagram(level=b'X')) is None
    assert wire.parse_log_record(_log_datagram(uid='')) is None
