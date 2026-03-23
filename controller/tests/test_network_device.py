"""Tests for NetworkDevice + UdpFrameReceiver + wire encoding."""

import logging
import socket
import struct
import threading

import pytest

from elemctl.device import DeviceFrame, DeviceState
from elemctl.network_device import NetworkDevice
from elemctl.udp_receiver import UdpFrameReceiver
from elemctl.wire import (
    CMD_ACK,
    CMD_CONFIGURE,
    CMD_DEBUG_SEEK,
    CMD_JUMP,
    CMD_LOAD,
    CMD_PAUSE,
    CMD_RESUME,
    CMD_START,
    CMD_STOP,
    UDP_FRAME_HEADER,
    encode_configure,
    encode_load,
    encode_start,
    parse_ack,
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

    def send_ack(self, status: int = 0) -> None:
        assert self._conn is not None
        msg = struct.pack('<IBB', 2, CMD_ACK, status)
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


def _expect_configure(
    endpoint: FakeEndpoint,
    *,
    device_id: int = 1,
    strip_length: int = 5,
    frame_port: int,
) -> None:
    cmd_type, payload = endpoint.read_command()
    assert cmd_type == CMD_CONFIGURE
    got_device_id, got_strip_length, got_frame_port = struct.unpack_from('<HHH', payload, 0)
    assert got_device_id == device_id
    assert got_strip_length == strip_length
    assert got_frame_port == frame_port
    endpoint.send_ack(0)


def _load_device(dev: NetworkDevice, ep: FakeEndpoint, gen: int = 1) -> bool:
    """Load with FakeEndpoint accepting + ACKing in a background thread."""
    result = [False]

    def server_side():
        ep.accept()
        _expect_configure(
            ep,
            device_id=dev._device_id,
            strip_length=dev._strip_length,
            frame_port=dev._frame_port,
        )
        ep.read_command()
        ep.send_ack(0)

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
    def test_encode_configure(self):
        msg = encode_configure(device_id=5, strip_length=10, frame_port=9002)
        length = struct.unpack_from('<I', msg, 0)[0]
        assert length == 7
        assert msg[4] == CMD_CONFIGURE
        device_id, strip_length, frame_port = struct.unpack_from('<HHH', msg, 5)
        assert device_id == 5
        assert strip_length == 10
        assert frame_port == 9002

    def test_encode_load(self):
        msg = encode_load(device_id=5, gen=2, blob=b'\xAA\xBB')
        # length prefix
        length = struct.unpack_from('<I', msg, 0)[0]
        assert length == 1 + 2 + 2 + 2  # type + device_id + gen + blob
        assert msg[4] == CMD_LOAD
        device_id, gen = struct.unpack_from('<HH', msg, 5)
        assert device_id == 5
        assert gen == 2
        assert msg[9:] == b'\xAA\xBB'

    def test_encode_start(self):
        msg = encode_start(t0_us=123456789)
        length = struct.unpack_from('<I', msg, 0)[0]
        assert length == 9
        assert msg[4] == CMD_START
        t0 = struct.unpack_from('<q', msg, 5)[0]
        assert t0 == 123456789

    def test_parse_ack_ok(self):
        data = struct.pack('<IBB', 2, CMD_ACK, 0)
        assert parse_ack(data) == 0

    def test_parse_ack_fail(self):
        data = struct.pack('<IBB', 2, CMD_ACK, 1)
        assert parse_ack(data) == 1

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


# =========================================================================
# 2. Connection
# =========================================================================


class TestConnection:
    def test_lazy_connect_on_load(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)
        assert dev.state() == DeviceState.IDLE
        assert not dev._connected

        assert _load_device(dev, endpoint)
        assert dev._connected
        assert dev.state() == DeviceState.LOADED

    def test_connect_sends_configure_first(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver, device_id=7, strip_length=9)

        def server_side():
            endpoint.accept()
            _expect_configure(
                endpoint,
                device_id=7,
                strip_length=9,
                frame_port=_receiver_port(receiver),
            )
            cmd_type, _payload = endpoint.read_command()
            assert cmd_type == CMD_LOAD
            endpoint.send_ack(0)

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

    def test_configure_ack_failure_disconnects(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)

        def server_side():
            endpoint.accept()
            cmd_type, _payload = endpoint.read_command()
            assert cmd_type == CMD_CONFIGURE
            endpoint.send_ack(1)

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
            _expect_configure(
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
    def test_load_sends_correct_wire_format(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver, device_id=7)

        def server_side():
            endpoint.accept()
            _expect_configure(
                endpoint,
                device_id=7,
                strip_length=5,
                frame_port=_receiver_port(receiver),
            )
            cmd_type, payload = endpoint.read_command()
            assert cmd_type == CMD_LOAD
            device_id, gen = struct.unpack_from('<HH', payload, 0)
            assert device_id == 7
            assert gen == 3
            assert payload[4:] == b'\xDE\xAD'
            endpoint.send_ack(0)

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
            _expect_configure(
                endpoint,
                device_id=1,
                strip_length=5,
                frame_port=_receiver_port(receiver),
            )
            endpoint.read_command()
            endpoint.send_ack(1)  # decode error

        t = threading.Thread(target=server_side, daemon=True)
        t.start()
        assert not dev.load(b'\x00', 1)
        t.join(timeout=3.0)

        assert dev.state() == DeviceState.IDLE

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

        # Second load — device rejects it
        def server_side():
            endpoint.read_command()
            endpoint.send_ack(1)  # decode error

        t = threading.Thread(target=server_side, daemon=True)
        t.start()
        assert not dev.load(b'\xBA\xD0', gen=2)
        t.join(timeout=3.0)

        # Should be IDLE, not stuck in PLAYING
        assert dev.state() == DeviceState.IDLE
        assert dev._last_t_rel == 0.0

    def test_reload_recovery_after_ack_failure(self, endpoint, receiver):
        """After a rejected load, a subsequent load on the same connection succeeds."""
        dev = _make_device(endpoint, receiver)
        assert _load_device(dev, endpoint, gen=1)

        # Second load — rejected
        def reject():
            endpoint.read_command()
            endpoint.send_ack(1)

        t = threading.Thread(target=reject, daemon=True)
        t.start()
        assert not dev.load(b'\xBA\xD0', gen=2)
        t.join(timeout=3.0)
        assert dev.state() == DeviceState.IDLE

        # Third load — accepted, same TCP connection
        def accept():
            endpoint.read_command()
            endpoint.send_ack(0)

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
            _expect_configure(
                endpoint,
                device_id=1,
                strip_length=5,
                frame_port=_receiver_port(receiver),
            )
            endpoint.read_command()
            # Send ACK in two separate writes to simulate fragmentation
            ack = struct.pack('<IBB', 2, CMD_ACK, 0)
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
    def test_esp32_logs_first_frame_received_after_start(self, endpoint, receiver, caplog):
        dev = _make_device(endpoint, receiver, device_type='esp32')
        assert _load_device(dev, endpoint)

        dev.start(_sec(0.0))
        endpoint.read_command()

        rgb = b'\xFF\x00\x00' * 5
        endpoint.send_udp_frame(device_id=1, gen=1, frame_index=0, t_rel=0.5, rgb=rgb)

        import time
        time.sleep(0.05)

        receiver.poll()
        caplog.set_level(logging.INFO)
        dev.tick_once(_sec(1.0))

        assert 'first frame received' in caplog.text
        assert 'device 1' in caplog.text
        assert 'frame_index=0' in caplog.text

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

    def test_update_while_connected_disconnects(self, endpoint, receiver):
        dev = _make_device(endpoint, receiver)
        assert _load_device(dev, endpoint)
        assert dev._connected

        dev.update_address('192.168.1.1', 5555)
        assert not dev._connected
        assert dev._host == '192.168.1.1'
        assert dev._tcp_port == 5555


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
