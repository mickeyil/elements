"""Tests for NetworkDevice + UdpFrameReceiver + wire encoding."""

import logging
import socket
import struct
import threading
import zlib

import pytest

import elemctl.deprecated.network_device as network_device_module
from elemctl.deprecated.device import DeviceFrame, DeviceState
from elemctl.deprecated.network_device import NetworkDevice
from elemctl.deprecated.udp_receiver import UdpFrameReceiver
from elemctl.deprecated.device_protocol import (
    ACK_ERROR,
    ACK_OK,
    ACK_WRONG_STATE,
    CMD_ACK,
    MAX_LOAD_BLOB_BYTES,
    CMD_ATTACH,
    CMD_SET_PROFILE,
    CMD_DEBUG_SEEK,
    CMD_JUMP,
    CMD_LOAD,
    CMD_PAUSE,
    CMD_STORE_BACKGROUND,
    CMD_CLEAR_BACKGROUND,
    CMD_QUERY_DEVICE_STATUS,
    CMD_REBOOT,
    CMD_RESUME,
    CMD_SYNC_RESULT,
    CMD_START,
    CMD_STOP,
    SYNC_REQ,
    SYNC_REQ_STRUCT,
    SYNC_RESP,
    UDP_FRAME_HEADER,
    encode_attach,
    encode_clear_background,
    encode_jump,
    encode_load,
    encode_query_device_status,
    encode_reboot,
    encode_set_profile,
    encode_store_background,
    encode_sync_req,
    encode_start,
    parse_ack,
    parse_ack_with_payload,
    parse_device_status_payload,
    parse_sync_resp,
    parse_udp_frame,
)


# ---------------------------------------------------------------------------
# FakeEndpoint — simulates a device's network side
# ---------------------------------------------------------------------------


class FakeEndpoint:
    """Simulates a device's TCP listener and can send UDP frames."""

    def __init__(self, udp_dest_port: int):
        self._tcp_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._tcp_srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._tcp_srv.bind(('127.0.0.1', 0))
        self._tcp_srv.listen(1)
        self._tcp_srv.settimeout(2.0)
        self.tcp_port: int = self._tcp_srv.getsockname()[1]

        self._udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._udp_dest = ('127.0.0.1', udp_dest_port)

        self._conn: socket.socket | None = None

    def accept(self) -> None:
        self._conn, _ = self._tcp_srv.accept()
        self._conn.settimeout(2.0)

    def accept_async(self) -> threading.Thread:
        """Accept in a background thread (for non-blocking test flow)."""
        t = threading.Thread(target=self.accept, daemon=True)
        t.start()
        return t

    def read_command(self) -> tuple[int, bytes]:
        """Read one length-prefixed command. Returns (cmd_type, payload)."""
        assert self._conn is not None
        hdr = self._recv_exact(4)
        length = struct.unpack('<I', hdr)[0]
        body = self._recv_exact(length)
        return body[0], body[1:]

    def send_ack(self, status: int = ACK_OK, payload: bytes = b'') -> None:
        assert self._conn is not None
        msg = struct.pack('<IBB', 2 + len(payload), CMD_ACK, status) + payload
        self._conn.sendall(msg)

    def send_udp_frame(
        self,
        device_id: int,
        gen: int,
        frame_index: int,
        t_rel: float,
        rgb: bytes,
    ) -> None:
        pkt = UDP_FRAME_HEADER.pack(device_id, gen, frame_index, t_rel) + rgb
        self._udp.sendto(pkt, self._udp_dest)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
        self._tcp_srv.close()
        self._udp.close()

    def _recv_exact(self, n: int) -> bytes:
        assert self._conn is not None
        buf = bytearray()
        while len(buf) < n:
            chunk = self._conn.recv(n - len(buf))
            if not chunk:
                raise ConnectionError('connection closed')
            buf.extend(chunk)
        return bytes(buf)


class _TimeoutRecordingSocket:
    def __init__(self, ack_count: int):
        ack = struct.pack('<IBB', 2, CMD_ACK, ACK_OK)
        self._recv_buf = bytearray(ack * ack_count)
        self.timeouts: list[float] = []
        self.sent: list[bytes] = []
        self.connected_to = None
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        self.timeouts.append(timeout)

    def connect(self, addr) -> None:
        self.connected_to = addr

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, n: int) -> bytes:
        if not self._recv_buf:
            return b''
        chunk = self._recv_buf[:n]
        del self._recv_buf[:n]
        return bytes(chunk)

    def close(self) -> None:
        self.closed = True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def receiver():
    """Create a UdpFrameReceiver on an ephemeral port."""
    # Bind to port 0 for ephemeral
    r = UdpFrameReceiver(0)
    yield r
    r.close()


def _receiver_port(r: UdpFrameReceiver) -> int:
    return r._sock.getsockname()[1]


@pytest.fixture()
def endpoint(receiver):
    """Create a FakeEndpoint that sends UDP to the receiver's port."""
    ep = FakeEndpoint(_receiver_port(receiver))
    yield ep
    ep.close()


def _make_device(
    endpoint: FakeEndpoint,
    receiver: UdpFrameReceiver,
    device_id: int = 1,
    device_type: str = 'sim',
    strip_length: int = 5,
) -> NetworkDevice:
    return NetworkDevice(
        device_id=device_id,
        host='127.0.0.1',
        tcp_port=endpoint.tcp_port,
        device_type=device_type,
        strip_length=strip_length,
        frame_port=_receiver_port(receiver),
        udp_receiver=receiver,
    )


def _expect_set_profile(
    endpoint: FakeEndpoint,
    *,
    strip_length: int = 5,
) -> None:
    cmd_type, payload = endpoint.read_command()
    assert cmd_type == CMD_SET_PROFILE
    (got_strip_length,) = struct.unpack_from('<H', payload, 0)
    assert got_strip_length == strip_length
    endpoint.send_ack(ACK_OK)


def _expect_attach(
    endpoint: FakeEndpoint,
    *,
    device_id: int = 1,
    frame_port: int,
) -> None:
    cmd_type, payload = endpoint.read_command()
    assert cmd_type == CMD_ATTACH
    got_device_id, got_frame_port = struct.unpack_from('<HH', payload, 0)
    assert got_device_id == device_id
    assert got_frame_port == frame_port
    endpoint.send_ack(ACK_OK)


def _expect_handshake(
    endpoint: FakeEndpoint,
    *,
    device_id: int = 1,
    strip_length: int = 5,
    frame_port: int,
) -> None:
    _expect_set_profile(endpoint, strip_length=strip_length)
    _expect_attach(endpoint, device_id=device_id, frame_port=frame_port)


def _load_device(dev: NetworkDevice, ep: FakeEndpoint, gen: int = 1) -> bool:
    """Load with FakeEndpoint accepting + ACKing in a background thread."""
    result = [False]

    def server_side():
        ep.accept()
        _expect_handshake(
            ep,
            device_id=dev._device_id,
            strip_length=dev._strip_length,
            frame_port=dev._frame_port,
        )
        ep.read_command()
        ep.send_ack(ACK_OK)

    t = threading.Thread(target=server_side, daemon=True)
    t.start()
    result[0] = dev.load(b'\x00\x01\x02', gen)
    t.join(timeout=3.0)
    return result[0]


def _sec(t: float) -> int:
    """Seconds to nanoseconds."""
    return int(t * 1e9)


# =========================================================================
# 1. Wire encoding
# =========================================================================


class TestWireEncoding:
    def test_encode_set_profile(self):
        msg = encode_set_profile(strip_length=10)
        length = struct.unpack_from('<I', msg, 0)[0]
        assert length == 3
        assert msg[4] == CMD_SET_PROFILE
        (strip_length,) = struct.unpack_from('<H', msg, 5)
        assert strip_length == 10

    def test_encode_attach(self):
        msg = encode_attach(device_id=5, frame_port=9002)
        length = struct.unpack_from('<I', msg, 0)[0]
        assert length == 5
        assert msg[4] == CMD_ATTACH
        device_id, frame_port = struct.unpack_from('<HH', msg, 5)
        assert device_id == 5
        assert frame_port == 9002

    def test_encode_load(self):
        msg = encode_load(gen=2, blob=b'\xAA\xBB')
        # length prefix
        length = struct.unpack_from('<I', msg, 0)[0]
        assert length == 1 + 2 + 2  # type + gen + blob
        assert msg[4] == CMD_LOAD
        (gen,) = struct.unpack_from('<H', msg, 5)
        assert gen == 2
        assert msg[7:] == b'\xAA\xBB'

    def test_encode_load_rejects_out_of_range_gen(self):
        with pytest.raises(ValueError, match='gen must fit in u16'):
            encode_load(gen=0x10000, blob=b'\x00')

    def test_encode_load_accepts_max_blob(self):
        blob = b'\xAA' * MAX_LOAD_BLOB_BYTES
        msg = encode_load(gen=2, blob=blob)
        length = struct.unpack_from('<I', msg, 0)[0]
        assert length == 1 + 2 + len(blob)
        assert msg[4] == CMD_LOAD

    def test_encode_load_rejects_oversize_blob(self):
        with pytest.raises(ValueError, match='compiled blob too large'):
            encode_load(gen=2, blob=b'\x00' * (MAX_LOAD_BLOB_BYTES + 1))

    def test_encode_jump_rejects_out_of_range_gen(self):
        with pytest.raises(ValueError, match='gen must fit in u16'):
            encode_jump(t0_us=123, t_rel=0.5, gen=0x10000)

    def test_encode_start(self):
        msg = encode_start(t0_us=123456789)
        length = struct.unpack_from('<I', msg, 0)[0]
        assert length == 9
        assert msg[4] == CMD_START
        t0 = struct.unpack_from('<q', msg, 5)[0]
        assert t0 == 123456789

    def test_parse_ack_ok(self):
        data = struct.pack('<IBB', 2, CMD_ACK, ACK_OK)
        assert parse_ack(data) == ACK_OK

    def test_parse_ack_fail(self):
        data = struct.pack('<IBB', 2, CMD_ACK, ACK_ERROR)
        assert parse_ack(data) == ACK_ERROR

    def test_parse_ack_wrong_state(self):
        data = struct.pack('<IBB', 2, CMD_ACK, ACK_WRONG_STATE)
        assert parse_ack(data) == ACK_WRONG_STATE

    def test_encode_reboot(self):
        msg = encode_reboot()
        length = struct.unpack_from('<I', msg, 0)[0]
        assert length == 1
        assert msg[4] == CMD_REBOOT

    def test_encode_sync_req(self):
        msg = encode_sync_req(seq=7, boot_token=1234, t1_us=5678)
        pkt_type, seq, boot_token, t1_us = SYNC_REQ_STRUCT.unpack(msg)
        assert pkt_type == SYNC_REQ
        assert seq == 7
        assert boot_token == 1234
        assert t1_us == 5678

    def test_parse_sync_resp(self):
        data = struct.pack('<BHIqqq', SYNC_RESP, 9, 44, 1000, 1200, 1300)
        assert parse_sync_resp(data) == (9, 44, 1000, 1200, 1300)

    def test_parse_ack_too_short(self):
        assert parse_ack(b'\x00') is None

    def test_parse_udp_frame(self):
        rgb = b'\xFF\x00\x00' * 5
        pkt = UDP_FRAME_HEADER.pack(3, 7, 42, 1.5) + rgb
        result = parse_udp_frame(pkt)
        assert result is not None
        device_id, frame = result
        assert device_id == 3
        assert frame.gen == 7
        assert frame.frame_index == 42
        assert frame.t_rel == pytest.approx(1.5)
        assert frame.rgb == rgb

    def test_parse_udp_frame_too_short(self):
        assert parse_udp_frame(b'\x00\x01') is None

    def test_encode_store_background(self):
        msg = encode_store_background(strip_length=5, crc32=0x12345678, blob=b'\xAA\xBB')
        length = struct.unpack_from('<I', msg, 0)[0]
        assert length == 1 + 2 + 4 + 2
        assert msg[4] == CMD_STORE_BACKGROUND
        strip_length, crc32 = struct.unpack_from('<HI', msg, 5)
        assert strip_length == 5
        assert crc32 == 0x12345678
        assert msg[11:] == b'\xAA\xBB'

    def test_encode_clear_background(self):
        msg = encode_clear_background()
        length = struct.unpack_from('<I', msg, 0)[0]
        assert length == 1
        assert msg[4] == CMD_CLEAR_BACKGROUND

    def test_encode_query_device_status(self):
        msg = encode_query_device_status()
        length = struct.unpack_from('<I', msg, 0)[0]
        assert length == 1
        assert msg[4] == CMD_QUERY_DEVICE_STATUS

    def test_python_crc32_matches_standard_check_vector(self):
        assert zlib.crc32(b"123456789") & 0xFFFFFFFF == 0xCBF43926

    def test_parse_ack_with_payload(self):
        data = struct.pack('<IBB', 16, CMD_ACK, ACK_OK) + struct.pack(
            '<BBHHII', 3, 0x03, 8, 8, 60, 0x133CB4BD
        )
        assert parse_ack(data) == ACK_OK
        assert parse_ack_with_payload(data) == (
            ACK_OK,
            struct.pack('<BBHHII', 3, 0x03, 8, 8, 60, 0x133CB4BD),
        )

    def test_parse_device_status_payload(self):
        payload = struct.pack('<BBHHII', 3, 0x03, 8, 8, 60, 0x133CB4BD)
        assert parse_device_status_payload(payload) == {
            'mode': 'detached_background',
            'profile_present': True,
            'profile_strip_length': 8,
            'background_present': True,
            'background_strip_length': 8,
            'background_blob_len': 60,
            'background_crc32': 0x133CB4BD,
        }


# =========================================================================
# 2. Connection
# =========================================================================


class TestConnection:
    def test_ensure_connected_uses_probe_and_handshake_timeouts(self, receiver, monkeypatch):
        fake_sock = _TimeoutRecordingSocket(ack_count=2)
        monkeypatch.setattr(
            'elemctl.deprecated.network_device.socket.socket',
            lambda *args, **kwargs: fake_sock,
        )

        dev = NetworkDevice(
            device_id=1,
            host='127.0.0.1',
            tcp_port=9001,
            device_type='esp32',
            strip_length=5,
            frame_port=_receiver_port(receiver),
            udp_receiver=receiver,
        )

        assert dev.ensure_connected()
        assert fake_sock.connected_to == ('127.0.0.1', 9001)
        assert fake_sock.timeouts == [
            network_device_module._PROBE_CONNECT_TIMEOUT,
            network_device_module._HANDSHAKE_ACK_TIMEOUT,
            network_device_module._HANDSHAKE_ACK_TIMEOUT,
            network_device_module._HANDSHAKE_ACK_TIMEOUT,
            network_device_module._HANDSHAKE_ACK_TIMEOUT,
        ]

    def test_lazy_connect_on_load(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)
        assert dev.state() == DeviceState.IDLE
        assert not dev._connected

        assert _load_device(dev, endpoint)
        assert dev._connected
        assert dev.state() == DeviceState.LOADED

    def test_connect_sends_profile_then_attach(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver, device_id=7, strip_length=9)

        def server_side():
            endpoint.accept()
            _expect_handshake(
                endpoint,
                device_id=7,
                strip_length=9,
                frame_port=_receiver_port(receiver),
            )
            cmd_type, _payload = endpoint.read_command()
            assert cmd_type == CMD_LOAD
            endpoint.send_ack(ACK_OK)

        t = threading.Thread(target=server_side, daemon=True)
        t.start()
        assert dev.load(b'\x00', 1)
        t.join(timeout=3.0)

    def test_connect_failure(self, receiver):
        dev = NetworkDevice(
            device_id=1,
            host='127.0.0.1',
            tcp_port=1,  # unlikely to be listening
            device_type='sim',
            strip_length=5,
            frame_port=_receiver_port(receiver),
            udp_receiver=receiver,
        )
        assert not dev.load(b'\x00', 1)
        assert dev.state() == DeviceState.IDLE

    def test_set_profile_ack_failure_disconnects(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)

        def server_side():
            endpoint.accept()
            cmd_type, _payload = endpoint.read_command()
            assert cmd_type == CMD_SET_PROFILE
            endpoint.send_ack(ACK_ERROR)

        t = threading.Thread(target=server_side, daemon=True)
        t.start()
        assert not dev.load(b'\x00', 1)
        t.join(timeout=3.0)
        assert not dev._connected

    def test_attach_ack_failure_disconnects(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)

        def server_side():
            endpoint.accept()
            _expect_set_profile(endpoint, strip_length=dev._strip_length)
            cmd_type, _payload = endpoint.read_command()
            assert cmd_type == CMD_ATTACH
            endpoint.send_ack(ACK_ERROR)

        t = threading.Thread(target=server_side, daemon=True)
        t.start()
        assert not dev.load(b'\x00', 1)
        t.join(timeout=3.0)
        assert not dev._connected

    def test_initial_connect_failure_logs_only_once(self, receiver, caplog):
        dev = NetworkDevice(
            device_id=1,
            host='127.0.0.1',
            tcp_port=1,
            device_type='sim',
            strip_length=5,
            frame_port=_receiver_port(receiver),
            udp_receiver=receiver,
        )
        with caplog.at_level(logging.WARNING):
            assert not dev.ensure_connected()
            assert not dev.ensure_connected()

        warnings = [
            rec.getMessage() for rec in caplog.records
            if 'connect to 127.0.0.1:1 failed' in rec.getMessage()
        ]
        assert len(warnings) == 1

    def test_reconnect_failures_after_prior_success_are_quiet(self, endpoint, receiver, caplog):
        dev = _make_device(endpoint, receiver)

        def server_side():
            endpoint.accept()
            _expect_handshake(
                endpoint,
                device_id=dev._device_id,
                strip_length=dev._strip_length,
                frame_port=dev._frame_port,
            )

        t = threading.Thread(target=server_side, daemon=True)
        t.start()
        assert dev.ensure_connected()
        t.join(timeout=3.0)

        dev.update_address('127.0.0.1', 1)

        with caplog.at_level(logging.WARNING):
            assert not dev.ensure_connected()
            assert not dev.ensure_connected()

        warnings = [
            rec.getMessage() for rec in caplog.records
            if 'connect to 127.0.0.1:1 failed' in rec.getMessage()
        ]
        assert warnings == []


# =========================================================================
# 3. Load
# =========================================================================


class TestLoad:
    def test_load_uses_command_connect_and_ack_timeouts(self, receiver, monkeypatch):
        fake_sock = _TimeoutRecordingSocket(ack_count=3)
        monkeypatch.setattr(
            'elemctl.deprecated.network_device.socket.socket',
            lambda *args, **kwargs: fake_sock,
        )

        dev = NetworkDevice(
            device_id=1,
            host='127.0.0.1',
            tcp_port=9001,
            device_type='esp32',
            strip_length=5,
            frame_port=_receiver_port(receiver),
            udp_receiver=receiver,
        )

        assert dev.load(b'\x00', gen=1)
        assert fake_sock.timeouts == [
            network_device_module._COMMAND_CONNECT_TIMEOUT,
            network_device_module._HANDSHAKE_ACK_TIMEOUT,
            network_device_module._HANDSHAKE_ACK_TIMEOUT,
            network_device_module._HANDSHAKE_ACK_TIMEOUT,
            network_device_module._HANDSHAKE_ACK_TIMEOUT,
            network_device_module._COMMAND_ACK_TIMEOUT,
            network_device_module._COMMAND_ACK_TIMEOUT,
        ]

    def test_load_sends_correct_wire_format(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver, device_id=7)

        def server_side():
            endpoint.accept()
            _expect_handshake(
                endpoint,
                device_id=7,
                strip_length=5,
                frame_port=_receiver_port(receiver),
            )
            cmd_type, payload = endpoint.read_command()
            assert cmd_type == CMD_LOAD
            (gen,) = struct.unpack_from('<H', payload, 0)
            assert gen == 3
            assert payload[2:] == b'\xDE\xAD'
            endpoint.send_ack(ACK_OK)

        t = threading.Thread(target=server_side, daemon=True)
        t.start()
        assert dev.load(b'\xDE\xAD', gen=3)
        t.join(timeout=3.0)

        assert dev.state() == DeviceState.LOADED
        assert dev._gen == 3

    def test_load_ack_failure(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)

        def server_side():
            endpoint.accept()
            _expect_handshake(
                endpoint,
                device_id=1,
                strip_length=5,
                frame_port=_receiver_port(receiver),
            )
            endpoint.read_command()
            endpoint.send_ack(ACK_ERROR)  # decode error

        t = threading.Thread(target=server_side, daemon=True)
        t.start()
        assert not dev.load(b'\x00', 1)
        t.join(timeout=3.0)

        assert dev.state() == DeviceState.IDLE


# =========================================================================
# 4. Reboot
# =========================================================================


class TestReboot:
    def test_reboot_sends_wire_command_and_waits_for_ack(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver, device_type='esp32')

        def server_side():
            endpoint.accept()
            _expect_handshake(
                endpoint,
                device_id=dev._device_id,
                strip_length=dev._strip_length,
                frame_port=dev._frame_port,
            )
            cmd_type, payload = endpoint.read_command()
            assert cmd_type == CMD_REBOOT
            assert payload == b''
            endpoint.send_ack(ACK_OK)

        t = threading.Thread(target=server_side, daemon=True)
        t.start()
        assert dev.reboot() is True
        t.join(timeout=3.0)
        assert not dev.is_connected

    def test_send_sync_result_writes_tcp_command(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver, device_type='esp32')

        def server_side():
            endpoint.accept()
            _expect_handshake(
                endpoint,
                device_id=dev._device_id,
                strip_length=dev._strip_length,
                frame_port=dev._frame_port,
            )
            cmd_type, payload = endpoint.read_command()
            assert cmd_type == CMD_SYNC_RESULT
            seq, boot_token, offset_us = struct.unpack('<HIq', payload)
            assert seq == 5
            assert boot_token == 1234
            assert offset_us == -2200

        t = threading.Thread(target=server_side, daemon=True)
        t.start()
        assert dev.send_sync_result(5, 1234, -2200) is True
        t.join(timeout=3.0)

    def test_store_background_writes_tcp_command(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver, device_type='esp32')

        def server_side():
            endpoint.accept()
            _expect_handshake(
                endpoint,
                device_id=dev._device_id,
                strip_length=dev._strip_length,
                frame_port=dev._frame_port,
            )
            cmd_type, payload = endpoint.read_command()
            assert cmd_type == CMD_STORE_BACKGROUND
            strip_length, crc32 = struct.unpack('<HI', payload[:6])
            assert strip_length == 5
            assert crc32 == 0xCBF43926
            assert payload[6:] == b"123456789"
            endpoint.send_ack(ACK_OK)

        t = threading.Thread(target=server_side, daemon=True)
        t.start()
        assert dev.store_background(b"123456789", 5, 0xCBF43926) is True
        t.join(timeout=3.0)

    def test_clear_background_writes_tcp_command(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver, device_type='esp32')

        def server_side():
            endpoint.accept()
            _expect_handshake(
                endpoint,
                device_id=dev._device_id,
                strip_length=dev._strip_length,
                frame_port=dev._frame_port,
            )
            cmd_type, payload = endpoint.read_command()
            assert cmd_type == CMD_CLEAR_BACKGROUND
            assert payload == b''
            endpoint.send_ack(ACK_OK)

        t = threading.Thread(target=server_side, daemon=True)
        t.start()
        assert dev.clear_background() is True
        t.join(timeout=3.0)

    def test_query_device_status_reads_ack_payload(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver, device_type='esp32')

        def server_side():
            endpoint.accept()
            _expect_handshake(
                endpoint,
                device_id=dev._device_id,
                strip_length=dev._strip_length,
                frame_port=dev._frame_port,
            )
            cmd_type, payload = endpoint.read_command()
            assert cmd_type == CMD_QUERY_DEVICE_STATUS
            assert payload == b''
            endpoint.send_ack(
                ACK_OK,
                struct.pack('<BBHHII', 3, 0x03, 8, 8, 60, 0x133CB4BD),
            )

        t = threading.Thread(target=server_side, daemon=True)
        t.start()
        assert dev.query_device_status() == {
            'mode': 'detached_background',
            'profile_present': True,
            'profile_strip_length': 8,
            'background_present': True,
            'background_strip_length': 8,
            'background_blob_len': 60,
            'background_crc32': 0x133CB4BD,
        }
        t.join(timeout=3.0)

    def test_reload_ack_failure_resets_state(self, endpoint, receiver):
        """After a successful load, a rejected reload should reset to IDLE."""
        dev = _make_device(endpoint, receiver)
        assert _load_device(dev, endpoint, gen=1)
        assert dev.state() == DeviceState.LOADED
        assert dev._gen == 1

        # Start playback to get into a non-trivial state
        dev.start(_sec(0.0))
        endpoint.read_command()  # consume START
        assert dev.state() == DeviceState.PLAYING

        endpoint.send_udp_frame(
            device_id=dev._device_id,
            gen=dev._gen,
            frame_index=7,
            t_rel=0.25,
            rgb=b'\x01\x02\x03' * 5,
        )
        import time
        time.sleep(0.05)
        receiver.poll()
        dev.tick_once(_sec(1.0))
        assert len(dev.drain_frames()) == 1

        endpoint.send_udp_frame(
            device_id=dev._device_id,
            gen=dev._gen,
            frame_index=8,
            t_rel=0.30,
            rgb=b'\x03\x02\x01' * 5,
        )
        time.sleep(0.05)
        receiver.poll()
        dev.tick_once(_sec(1.1))
        assert dev._last_t_rel == pytest.approx(0.30)

        # Second load — device rejects it
        def server_side():
            endpoint.read_command()
            endpoint.send_ack(ACK_ERROR)  # decode error

        t = threading.Thread(target=server_side, daemon=True)
        t.start()
        assert not dev.load(b'\xBA\xD0', gen=2)
        t.join(timeout=3.0)

        # Should be IDLE, not stuck in PLAYING
        assert dev.state() == DeviceState.IDLE
        assert dev._last_t_rel == 0.0
        assert dev.drain_frames() == []

    def test_successful_reload_clears_stale_runtime_caches(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)
        assert _load_device(dev, endpoint, gen=1)

        endpoint.send_udp_frame(
            device_id=dev._device_id,
            gen=dev._gen,
            frame_index=4,
            t_rel=0.2,
            rgb=b'\x01\x00\x00' * 5,
        )
        import time
        time.sleep(0.05)
        receiver.poll()
        dev.tick_once(_sec(1.0))
        assert len(dev.drain_frames()) == 1

        endpoint.send_udp_frame(
            device_id=dev._device_id,
            gen=dev._gen,
            frame_index=5,
            t_rel=0.3,
            rgb=b'\x00\x01\x00' * 5,
        )
        time.sleep(0.05)
        receiver.poll()
        dev.tick_once(_sec(1.1))
        assert dev._last_t_rel == pytest.approx(0.3)

        def accept():
            endpoint.read_command()
            endpoint.send_ack(ACK_OK)

        t = threading.Thread(target=accept, daemon=True)
        t.start()
        assert dev.load(b'\x00', gen=3)
        t.join(timeout=3.0)

        assert dev.state() == DeviceState.LOADED
        assert dev._gen == 3
        assert dev.drain_frames() == []

    def test_reload_recovery_after_ack_failure(self, endpoint, receiver):
        """After a rejected load, a subsequent load on the same connection succeeds."""
        dev = _make_device(endpoint, receiver)
        assert _load_device(dev, endpoint, gen=1)

        # Second load — rejected
        def reject():
            endpoint.read_command()
            endpoint.send_ack(ACK_ERROR)

        t = threading.Thread(target=reject, daemon=True)
        t.start()
        assert not dev.load(b'\xBA\xD0', gen=2)
        t.join(timeout=3.0)
        assert dev.state() == DeviceState.IDLE

        # Third load — accepted, same TCP connection
        def accept():
            endpoint.read_command()
            endpoint.send_ack(ACK_OK)

        t = threading.Thread(target=accept, daemon=True)
        t.start()
        assert dev.load(b'\x00', gen=3)
        t.join(timeout=3.0)
        assert dev.state() == DeviceState.LOADED
        assert dev._gen == 3

    def test_load_partial_ack_read(self, endpoint, receiver):
        """ACK arriving in multiple TCP segments should still be read correctly."""
        dev = _make_device(endpoint, receiver)

        def server_side():
            endpoint.accept()
            _expect_handshake(
                endpoint,
                device_id=1,
                strip_length=5,
                frame_port=_receiver_port(receiver),
            )
            endpoint.read_command()
            # Send ACK in two separate writes to simulate fragmentation
            ack = struct.pack('<IBB', 2, CMD_ACK, ACK_OK)
            endpoint._conn.sendall(ack[:3])
            import time
            time.sleep(0.01)
            endpoint._conn.sendall(ack[3:])

        t = threading.Thread(target=server_side, daemon=True)
        t.start()
        assert dev.load(b'\x00', gen=1)
        t.join(timeout=3.0)

        assert dev.state() == DeviceState.LOADED


# =========================================================================
# 4. Commands — wire format + state
# =========================================================================


class TestCommands:
    def test_start(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)
        assert _load_device(dev, endpoint)

        dev.start(_sec(10.0))
        cmd_type, payload = endpoint.read_command()
        assert cmd_type == CMD_START
        t0 = struct.unpack_from('<q', payload, 0)[0]
        assert t0 == _sec(10.0) // 1000
        assert dev.state() == DeviceState.PLAYING

    def test_jump_from_playing(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)
        assert _load_device(dev, endpoint)

        dev.start(_sec(0.0))
        endpoint.read_command()  # consume START

        dev.jump(_sec(1.0), 2.0, 5)
        cmd_type, payload = endpoint.read_command()
        assert cmd_type == CMD_JUMP
        t0, t_rel, gen = struct.unpack_from('<qfH', payload, 0)
        assert t_rel == pytest.approx(2.0)
        assert gen == 5
        assert dev.state() == DeviceState.PLAYING  # stays PLAYING

    def test_jump_from_paused(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)
        assert _load_device(dev, endpoint)

        # Go to LOADED -> jump -> PAUSED
        dev.jump(_sec(1.0), 2.0, 5)
        endpoint.read_command()  # consume
        assert dev.state() == DeviceState.PAUSED
        assert dev._last_t_rel == pytest.approx(2.0)

    def test_pause(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)
        assert _load_device(dev, endpoint)

        dev.start(_sec(0.0))
        endpoint.read_command()  # consume START

        dev.pause(_sec(1.5))
        cmd_type, _payload = endpoint.read_command()
        assert cmd_type == CMD_PAUSE
        assert dev.state() == DeviceState.PAUSED
        assert dev._last_t_rel == pytest.approx(1.5)

    def test_resume(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)
        assert _load_device(dev, endpoint)

        dev.start(_sec(0.0))
        endpoint.read_command()

        dev.pause(_sec(1.0))
        endpoint.read_command()

        dev.resume(_sec(2.0))
        cmd_type, payload = endpoint.read_command()
        assert cmd_type == CMD_RESUME
        t0 = struct.unpack_from('<q', payload, 0)[0]
        assert t0 == _sec(2.0) // 1000
        assert dev.state() == DeviceState.PLAYING

    def test_stop(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)
        assert _load_device(dev, endpoint)

        dev.start(_sec(0.0))
        endpoint.read_command()

        dev.stop()
        cmd_type, _payload = endpoint.read_command()
        assert cmd_type == CMD_STOP
        assert dev.state() == DeviceState.LOADED

    def test_debug_seek(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver, device_type='sim')
        assert _load_device(dev, endpoint)

        dev.debug_seek(3.5, _sec(10.0))
        cmd_type, payload = endpoint.read_command()
        assert cmd_type == CMD_DEBUG_SEEK
        t_rel = struct.unpack_from('<f', payload, 0)[0]
        assert t_rel == pytest.approx(3.5)
        assert dev.state() == DeviceState.PAUSED
        assert dev._last_t_rel == pytest.approx(3.5)


# =========================================================================
# 5. State tracking — failed sends don't mutate state
# =========================================================================


class TestStateOnFailure:
    def test_start_failure_keeps_loaded(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)
        assert _load_device(dev, endpoint)

        # Force disconnect by closing the device's socket directly
        dev._sock.close()
        dev._sock = None
        dev._connected = False

        dev.start(_sec(0.0))
        assert dev.state() == DeviceState.LOADED  # unchanged

    def test_stop_failure_keeps_playing(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)
        assert _load_device(dev, endpoint)

        dev.start(_sec(0.0))
        endpoint.read_command()  # consume START
        assert dev.state() == DeviceState.PLAYING

        dev._sock.close()
        dev._sock = None
        dev._connected = False

        dev.stop()
        assert dev.state() == DeviceState.PLAYING  # unchanged


# =========================================================================
# 6. current_t_rel
# =========================================================================


class TestCurrentTRel:
    def test_idle_returns_zero(self, receiver):
        dev = NetworkDevice(
            device_id=1, host='127.0.0.1', tcp_port=1,
            device_type='sim', strip_length=5,
            frame_port=_receiver_port(receiver), udp_receiver=receiver,
        )
        assert dev.current_t_rel(_sec(10.0)) == 0.0

    def test_playing_computed_from_t0(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)
        assert _load_device(dev, endpoint)

        dev.start(_sec(1.0))
        endpoint.read_command()

        t_rel = dev.current_t_rel(_sec(3.5))
        assert t_rel == pytest.approx(2.5)

    def test_paused_returns_cached(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)
        assert _load_device(dev, endpoint)

        dev.start(_sec(0.0))
        endpoint.read_command()

        dev.pause(_sec(2.0))
        endpoint.read_command()

        # Even with advancing time, paused returns cached value
        assert dev.current_t_rel(_sec(100.0)) == pytest.approx(2.0)


# =========================================================================
# 7. Frames — UDP routing
# =========================================================================


class TestFrames:
    def test_frames_routed_by_device_id(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver, device_id=5)
        assert _load_device(dev, endpoint)

        rgb = b'\xFF\x00\x00' * 5
        endpoint.send_udp_frame(device_id=5, gen=1, frame_index=0, t_rel=0.5, rgb=rgb)
        # Wrong device_id — should be ignored
        endpoint.send_udp_frame(device_id=99, gen=1, frame_index=1, t_rel=0.6, rgb=rgb)

        import time
        time.sleep(0.05)  # let UDP arrive

        receiver.poll()
        dev.tick_once(_sec(1.0))

        frames = dev.drain_frames()
        assert len(frames) == 1
        assert frames[0].frame_index == 0
        assert frames[0].t_rel == pytest.approx(0.5)
        assert frames[0].rgb == rgb

    def test_drain_clears_frames(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver, device_id=1)
        assert _load_device(dev, endpoint)

        endpoint.send_udp_frame(1, 1, 0, 0.1, b'\x00' * 15)

        import time
        time.sleep(0.05)

        receiver.poll()
        dev.tick_once(_sec(1.0))
        assert len(dev.drain_frames()) == 1
        assert len(dev.drain_frames()) == 0  # second drain is empty


# =========================================================================
# 8. Error handling
# =========================================================================


class TestErrors:
    def test_load_tcp_failure_returns_false(self, receiver):
        dev = NetworkDevice(
            device_id=1, host='127.0.0.1', tcp_port=1,
            device_type='sim', strip_length=5,
            frame_port=_receiver_port(receiver), udp_receiver=receiver,
        )
        assert not dev.load(b'\x00', 1)
        assert not dev._connected

    def test_send_failure_marks_disconnected(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)
        assert _load_device(dev, endpoint)
        assert dev._connected

        # Close the underlying socket to force send failure
        dev._sock.close()
        dev.start(_sec(0.0))
        assert not dev._connected

    def test_tick_once_detects_peer_disconnect(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)
        assert _load_device(dev, endpoint)
        assert dev._connected

        endpoint._conn.close()
        endpoint._conn = None

        dev.tick_once(_sec(1.0))
        assert not dev._connected

    def test_disconnect_transport_freezes_playback_position(self, endpoint, receiver, monkeypatch):
        dev = _make_device(endpoint, receiver)
        assert _load_device(dev, endpoint)

        dev.start(_sec(1.0))
        endpoint.read_command()

        monkeypatch.setattr('elemctl.deprecated.network_device.time.monotonic_ns', lambda: _sec(2.5))

        dev.disconnect_transport()

        assert not dev._connected
        assert dev.drain_frames() == []
        assert dev._t0_us == 0
        assert dev._last_t_rel == pytest.approx(1.5)
        assert dev.current_t_rel(_sec(99.0)) == pytest.approx(1.5)

    def test_disconnect_transport_preserves_last_t_rel_when_playing_without_t0(self, receiver):
        dev = NetworkDevice(
            device_id=1, host='127.0.0.1', tcp_port=1,
            device_type='sim', strip_length=5,
            frame_port=_receiver_port(receiver), udp_receiver=receiver,
        )
        dev._connected = True
        dev._state = DeviceState.PLAYING
        dev._t0_us = 0
        dev._last_t_rel = 1.25

        dev.disconnect_transport()

        assert not dev.is_connected
        assert dev._t0_us == 0
        assert dev._last_t_rel == pytest.approx(1.25)
        assert dev.current_t_rel(_sec(99.0)) == pytest.approx(1.25)

    def test_tick_once_detects_peer_disconnect_and_freezes_playback(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)
        assert _load_device(dev, endpoint)
        assert dev._connected

        dev.start(_sec(1.0))
        endpoint.read_command()

        endpoint._conn.close()
        endpoint._conn = None

        dev.tick_once(_sec(2.0))

        assert not dev._connected
        assert dev._t0_us == 0
        assert dev._last_t_rel == pytest.approx(1.0)
        assert dev.current_t_rel(_sec(99.0)) == pytest.approx(1.0)

    def test_load_wrong_state_disconnects_transport(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)

        def server_side():
            endpoint.accept()
            _expect_handshake(
                endpoint,
                device_id=dev._device_id,
                strip_length=dev._strip_length,
                frame_port=dev._frame_port,
            )
            endpoint.read_command()
            endpoint.send_ack(ACK_WRONG_STATE)

        t = threading.Thread(target=server_side, daemon=True)
        t.start()
        assert not dev.load(b'\x00', 1)
        t.join(timeout=3.0)

        assert dev.state() == DeviceState.IDLE
        assert not dev._connected
        assert dev.drain_frames() == []

    def test_ack_receipt_sets_activity_flag(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)
        assert _load_device(dev, endpoint)

        assert dev.consume_activity_observed() is True
        assert dev.consume_activity_observed() is False

    def test_send_only_does_not_set_activity_flag(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)
        assert _load_device(dev, endpoint)
        assert dev.consume_activity_observed() is True

        dev.start(_sec(0.0))

        assert dev.consume_activity_observed() is False

    def test_udp_frame_receipt_sets_activity_flag(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)
        assert _load_device(dev, endpoint)
        assert dev.consume_activity_observed() is True

        endpoint.send_udp_frame(
            device_id=1,
            gen=dev._gen,
            frame_index=0,
            t_rel=0.25,
            rgb=b'\x01\x02\x03' * 5,
        )

        import time
        time.sleep(0.05)

        receiver.poll()
        dev.tick_once(_sec(1.0))

        assert dev.consume_activity_observed() is True
        assert dev.consume_activity_observed() is False

    def test_malformed_udp_dropped(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver, device_id=1)
        assert _load_device(dev, endpoint)

        # Send garbage UDP
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.sendto(b'\x00\x01', ('127.0.0.1', _receiver_port(receiver)))
        udp.close()

        import time
        time.sleep(0.05)

        receiver.poll()
        dev.tick_once(_sec(1.0))
        assert dev.drain_frames() == []


# =========================================================================
# 9. supports_debug_seek
# =========================================================================


class TestCapability:
    def test_sim_produces_program_frames(self, receiver):
        dev = NetworkDevice(
            device_id=1, host='127.0.0.1', tcp_port=1,
            device_type='sim', strip_length=5,
            frame_port=_receiver_port(receiver), udp_receiver=receiver,
        )
        assert dev.produces_program_frames()

    def test_esp32_no_program_frames(self, receiver):
        dev = NetworkDevice(
            device_id=1, host='127.0.0.1', tcp_port=1,
            device_type='esp32', strip_length=5,
            frame_port=_receiver_port(receiver), udp_receiver=receiver,
        )
        assert not dev.produces_program_frames()

    def test_sim_supports_debug_seek(self, receiver):
        dev = NetworkDevice(
            device_id=1, host='127.0.0.1', tcp_port=1,
            device_type='sim', strip_length=5,
            frame_port=_receiver_port(receiver), udp_receiver=receiver,
        )
        assert dev.supports_debug_seek()

    def test_esp32_no_debug_seek(self, receiver):
        dev = NetworkDevice(
            device_id=1, host='127.0.0.1', tcp_port=1,
            device_type='esp32', strip_length=5,
            frame_port=_receiver_port(receiver), udp_receiver=receiver,
        )
        assert not dev.supports_debug_seek()


# =========================================================================
# 10. Poll ordering contract
# =========================================================================


# =========================================================================
# 11. update_address
# =========================================================================


class TestUpdateAddress:
    def test_no_change_is_noop(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)
        assert _load_device(dev, endpoint)
        assert dev._connected

        # Same address — should not disconnect
        dev.update_address('127.0.0.1', endpoint.tcp_port)
        assert dev._connected

    def test_update_while_disconnected(self, receiver):
        dev = NetworkDevice(
            device_id=1, host='', tcp_port=0,
            device_type='sim', strip_length=5,
            frame_port=_receiver_port(receiver), udp_receiver=receiver,
        )
        assert not dev._connected
        dev.update_address('127.0.0.1', 9999)
        assert dev._host == '127.0.0.1'
        assert dev._tcp_port == 9999

    def test_update_while_connected_disconnects_and_freezes_runtime(self, endpoint, receiver, monkeypatch):
        dev = _make_device(endpoint, receiver)
        assert _load_device(dev, endpoint)
        assert dev._connected

        dev.start(_sec(1.0))
        endpoint.read_command()

        monkeypatch.setattr('elemctl.deprecated.network_device.time.monotonic_ns', lambda: _sec(2.25))

        dev.update_address('192.168.1.1', 5555)
        assert not dev._connected
        assert dev._host == '192.168.1.1'
        assert dev._tcp_port == 5555
        assert dev._last_t_rel == pytest.approx(1.25)
        assert dev.current_t_rel(_sec(99.0)) == pytest.approx(1.25)


# =========================================================================
# 12. Unresolved address
# =========================================================================


class TestUnresolvedAddress:
    def test_ensure_connected_skips_empty_host(self, receiver):
        dev = NetworkDevice(
            device_id=1, host='', tcp_port=9001,
            device_type='sim', strip_length=5,
            frame_port=_receiver_port(receiver), udp_receiver=receiver,
        )
        assert not dev.ensure_connected()

    def test_ensure_connected_skips_zero_port(self, receiver):
        dev = NetworkDevice(
            device_id=1, host='127.0.0.1', tcp_port=0,
            device_type='sim', strip_length=5,
            frame_port=_receiver_port(receiver), udp_receiver=receiver,
        )
        assert not dev.ensure_connected()

    def test_ensure_connected_skips_both_unresolved(self, receiver):
        dev = NetworkDevice(
            device_id=1, host='', tcp_port=0,
            device_type='sim', strip_length=5,
            frame_port=_receiver_port(receiver), udp_receiver=receiver,
        )
        assert not dev.ensure_connected()


class TestPollOrdering:
    def test_no_poll_no_frames(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver, device_id=1)
        assert _load_device(dev, endpoint)

        endpoint.send_udp_frame(1, 1, 0, 0.5, b'\x00' * 15)

        import time
        time.sleep(0.05)

        # tick_once WITHOUT receiver.poll() first
        dev.tick_once(_sec(1.0))
        assert dev.drain_frames() == []

    def test_poll_then_tick_delivers_frames(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver, device_id=1)
        assert _load_device(dev, endpoint)

        endpoint.send_udp_frame(1, 1, 0, 0.5, b'\x00' * 15)

        import time
        time.sleep(0.05)

        # poll THEN tick
        receiver.poll()
        dev.tick_once(_sec(1.0))
        frames = dev.drain_frames()
        assert len(frames) == 1
        assert frames[0].t_rel == pytest.approx(0.5)
