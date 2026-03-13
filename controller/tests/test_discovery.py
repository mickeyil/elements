"""Tests for discovery wire format and DiscoveryReceiver."""

import socket
import time

import pytest

from elemctl.discovery import (
    DISCOVERY_MAGIC,
    DISCOVERY_REASON_DUPLICATE_UID,
    DISCOVERY_TYPE_REJECT,
    DiscoveryReceiver,
    encode_hello,
    encode_reject,
    parse_hello,
    parse_reject,
)


# =========================================================================
# 1. Wire format — encode/parse round-trip
# =========================================================================


class TestHelloCodec:
    def test_round_trip(self):
        pkt = encode_hello('sim-left', 9001)
        result = parse_hello(pkt)
        assert result is not None
        uid, tcp_port = result
        assert uid == 'sim-left'
        assert tcp_port == 9001

    def test_round_trip_unicode(self):
        pkt = encode_hello('dëvice-1', 8080)
        result = parse_hello(pkt)
        assert result is not None
        assert result[0] == 'dëvice-1'
        assert result[1] == 8080

    def test_parse_too_short(self):
        assert parse_hello(b'\x00\x01\x02') is None

    def test_parse_bad_magic(self):
        pkt = encode_hello('x', 9001)
        bad = b'\xFF\xFF' + pkt[2:]
        assert parse_hello(bad) is None

    def test_parse_truncated_uid(self):
        pkt = encode_hello('abcdef', 9001)
        # Truncate so uid_len says 6 but only 3 bytes follow
        assert parse_hello(pkt[:8]) is None

    def test_parse_zero_port(self):
        # Manually craft — encode_hello rejects port=0
        import struct
        pkt = struct.pack('<HHB', DISCOVERY_MAGIC, 0, 1) + b'x'
        assert parse_hello(pkt) is None

    def test_encode_rejects_zero_port(self):
        with pytest.raises(ValueError, match='non-zero'):
            encode_hello('x', 0)

    def test_encode_rejects_empty_uid(self):
        with pytest.raises(ValueError, match='non-empty'):
            encode_hello('', 9001)

    def test_encode_rejects_long_uid(self):
        with pytest.raises(ValueError, match='too long'):
            encode_hello('a' * 256, 9001)

    def test_parse_empty_uid(self):
        # Manually craft a packet with uid_len=0
        import struct
        pkt = struct.pack('<HHB', DISCOVERY_MAGIC, 9001, 0)
        assert parse_hello(pkt) is None

    def test_parse_invalid_utf8(self):
        import struct
        # Valid header but invalid UTF-8 bytes
        pkt = struct.pack('<HHB', DISCOVERY_MAGIC, 9001, 2) + b'\x80\x81'
        assert parse_hello(pkt) is None


class TestRejectCodec:
    def test_round_trip(self):
        pkt = encode_reject(DISCOVERY_REASON_DUPLICATE_UID)
        assert parse_reject(pkt) == DISCOVERY_REASON_DUPLICATE_UID

    def test_parse_reject_wrong_size(self):
        assert parse_reject(b'\x00\x01\x02') is None

    def test_parse_reject_wrong_magic(self):
        import struct
        pkt = struct.pack('<HBB', 0xFFFF, DISCOVERY_TYPE_REJECT, DISCOVERY_REASON_DUPLICATE_UID)
        assert parse_reject(pkt) is None


# =========================================================================
# 2. DiscoveryReceiver
# =========================================================================


@pytest.fixture()
def receiver():
    r = DiscoveryReceiver(0)  # ephemeral port
    yield r
    r.close()


def _receiver_port(r: DiscoveryReceiver) -> int:
    return r._sock.getsockname()[1]


class TestDiscoveryReceiver:
    def test_poll_delivers(self, receiver):
        port = _receiver_port(receiver)
        pkt = encode_hello('dev-1', 8888)

        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.sendto(pkt, ('127.0.0.1', port))
        udp.close()

        time.sleep(0.05)
        receiver.poll()
        discoveries = receiver.drain_discoveries()
        assert len(discoveries) == 1
        uid, host, tcp_port, reply_port = discoveries[0]
        assert uid == 'dev-1'
        assert host == '127.0.0.1'
        assert tcp_port == 8888
        assert reply_port != 0

    def test_ignores_garbage(self, receiver):
        port = _receiver_port(receiver)
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.sendto(b'\x00\x01\x02', ('127.0.0.1', port))
        udp.close()

        time.sleep(0.05)
        receiver.poll()
        assert receiver.drain_discoveries() == []

    def test_drain_clears(self, receiver):
        port = _receiver_port(receiver)
        pkt = encode_hello('dev-2', 7777)

        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.sendto(pkt, ('127.0.0.1', port))
        udp.close()

        time.sleep(0.05)
        receiver.poll()
        assert len(receiver.drain_discoveries()) == 1
        assert receiver.drain_discoveries() == []

    def test_multiple_hellos(self, receiver):
        port = _receiver_port(receiver)
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.sendto(encode_hello('a', 1000), ('127.0.0.1', port))
        udp.sendto(encode_hello('b', 2000), ('127.0.0.1', port))
        udp.close()

        time.sleep(0.05)
        receiver.poll()
        discoveries = receiver.drain_discoveries()
        assert len(discoveries) == 2
        uids = {d[0] for d in discoveries}
        assert uids == {'a', 'b'}

    def test_send_reject(self, receiver):
        port = _receiver_port(receiver)
        device = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        device.bind(('127.0.0.1', 0))
        device.settimeout(0.5)
        device.sendto(encode_hello('dev-3', 3333), ('127.0.0.1', port))

        time.sleep(0.05)
        receiver.poll()
        discoveries = receiver.drain_discoveries()
        assert len(discoveries) == 1
        _uid, host, _tcp_port, reply_port = discoveries[0]

        receiver.send_reject(host, reply_port, DISCOVERY_REASON_DUPLICATE_UID)
        data, _addr = device.recvfrom(16)
        assert parse_reject(data) == DISCOVERY_REASON_DUPLICATE_UID
        device.close()
