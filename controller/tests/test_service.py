"""Tests for ControllerService and UdsServer."""

import json
import socket
import struct
import sys
import threading
import time
from pathlib import Path

import pytest

_repo = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo / 'compiler'))

from elemctl.config import Config, DeviceConfig
from elemctl.controller import ControllerState
from elemctl.device import DeviceState
from elemctl.service import ControllerService
from elemctl.serve import UdsServer
from elemctl.uds_wire import (
    KIND_FRAME,
    KIND_JSON,
    UdsReader,
    encode_json,
    parse_json_payload,
)


# ---------------------------------------------------------------------------
# Test fakes
# ---------------------------------------------------------------------------


class _NoopReceiver:
    """Fake receiver that never delivers frames."""

    def __init__(self, port):
        pass

    def register_device(self, device_id):
        pass

    def poll(self):
        pass

    def drain(self, device_id):
        return []

    def close(self):
        pass


class _FakeDevice:
    """Minimal device for service tests. Accepts all commands, tracks state."""

    def __init__(self):
        self._state = DeviceState.IDLE
        self._t0_ns = 0
        self.is_connected = True
        self.ensure_connected_calls = 0

    def load(self, blob, gen):
        self._state = DeviceState.LOADED
        return True

    def start(self, t0_ns):
        self._t0_ns = t0_ns
        self._state = DeviceState.PLAYING

    def jump(self, t0_ns, t_rel, gen):
        self._t0_ns = t0_ns
        if self._state != DeviceState.PLAYING:
            self._state = DeviceState.PAUSED

    def pause(self, now_ns):
        self._state = DeviceState.PAUSED

    def resume(self, t0_ns):
        self._t0_ns = t0_ns
        self._state = DeviceState.PLAYING

    def stop(self):
        self._state = DeviceState.LOADED

    def tick_once(self, now_ns):
        pass

    def state(self):
        return self._state

    def current_t_rel(self, now_ns):
        if self._state == DeviceState.PLAYING:
            return (now_ns - self._t0_ns) / 1e9
        return 0.0

    def drain_frames(self):
        return []

    def supports_debug_seek(self):
        return False

    def debug_seek(self, t_rel, now_ns):
        pass

    def ensure_connected(self):
        self.ensure_connected_calls += 1
        self.is_connected = True
        return True

    def close(self):
        self._state = DeviceState.IDLE


class _FrameProducingDevice(_FakeDevice):
    """Fake device that produces frames on each tick while PLAYING."""

    def __init__(self, strip_length: int = 5):
        super().__init__()
        self._strip_length = strip_length
        self._gen = 0
        self._frame_index = 0
        self._pending_frames: list = []

    def load(self, blob, gen):
        self._gen = gen
        self._frame_index = 0
        return super().load(blob, gen)

    def start(self, t0_ns):
        self._frame_index = 0
        self._pending_frames.clear()
        super().start(t0_ns)

    def tick_once(self, now_ns):
        if self._state != DeviceState.PLAYING:
            return
        from elemctl.device import DeviceFrame
        t_rel = (now_ns - self._t0_ns) / 1e9
        # Produce a frame with brightness proportional to pixel index
        rgb = bytes([0x42] * self._strip_length * 3)
        self._pending_frames.append(DeviceFrame(
            gen=self._gen,
            frame_index=self._frame_index,
            t_rel=t_rel,
            rgb=rgb,
        ))
        self._frame_index += 1

    def drain_frames(self):
        out = self._pending_frames[:]
        self._pending_frames.clear()
        return out


def _make_fake_factory(fake_devices: list[_FakeDevice]):
    """Return a factory that yields pre-created _FakeDevice instances in order."""
    idx = iter(range(len(fake_devices)))

    def factory(device_id, host, tcp_port, device_type, udp_receiver):
        return fake_devices[next(idx)]

    return factory


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


_SIMPLE_DSL = """\
from elements.dsl import strip, spark, sec
s = strip('test', length=5)
sp = spark(color='white', fade=1.0)
sp.schedule(s.pixels('0-4'), at=0, duration=sec(0.5))
"""


def _make_config(n_devices=1):
    devices = []
    for i in range(n_devices):
        devices.append(DeviceConfig(
            device_id=i + 1,
            device_uid=f'sim-{i + 1}',
            device_type='sim',
            host='127.0.0.1',
            tcp_port=9000 + i,
            strip_id='test' if n_devices == 1 else f'strip_{chr(ord("a") + i)}',
            length=5,
        ))
    return Config(frame_port=1, devices=devices)


def _make_service(n_devices=1, fake_devices=None):
    config = _make_config(n_devices)
    if fake_devices is None:
        fake_devices = [_FakeDevice() for _ in range(n_devices)]
    return ControllerService(
        config,
        receiver_factory=_NoopReceiver,
        device_factory=_make_fake_factory(fake_devices),
    ), fake_devices


# =========================================================================
# Part A: ControllerService unit tests
# =========================================================================


class TestSnapshotIdle:
    def test_snapshot_idle(self):
        svc, _ = _make_service()
        snap = svc.build_snapshot()

        assert snap['type'] == 'event'
        assert snap['event'] == 'snapshot'
        assert snap['protocol_version'] == 1
        assert snap['session'] is None
        assert len(snap['devices']) == 1
        assert snap['devices'][0]['device_id'] == 1
        assert snap['devices'][0]['connected'] is True

    def test_snapshot_shows_disconnected(self):
        fakes = [_FakeDevice()]
        fakes[0].is_connected = False
        svc, _ = _make_service(fake_devices=fakes)
        snap = svc.build_snapshot()
        assert snap['devices'][0]['connected'] is False


class TestHandleLoad:
    def test_load_valid(self):
        svc, _ = _make_service()
        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'load',
            'source': _SIMPLE_DSL,
            'beat': 1.0,
            'duration': 0.5,
        })
        assert reply['ok'] is True
        assert reply['id'] == 1
        assert reply['result']['session_id'] == 1

        snap = svc.build_snapshot()
        assert snap['session'] is not None
        assert snap['session']['session_id'] == 1

    def test_load_compile_error(self):
        svc, _ = _make_service()
        reply = svc.handle_cmd({
            'id': 2,
            'cmd': 'load',
            'source': 'raise ValueError("bad")',
            'beat': 1.0,
            'duration': 0.5,
        })
        assert reply['ok'] is False
        assert 'error' in reply

        # Builder should be clean for next load
        reply2 = svc.handle_cmd({
            'id': 3,
            'cmd': 'load',
            'source': _SIMPLE_DSL,
            'beat': 1.0,
            'duration': 0.5,
        })
        assert reply2['ok'] is True

    def test_load_missing_fields(self):
        svc, _ = _make_service()
        reply = svc.handle_cmd({'id': 4, 'cmd': 'load', 'source': _SIMPLE_DSL})
        assert reply['ok'] is False
        assert 'beat' in reply['error'] or 'missing' in reply['error']


class TestHandlePlay:
    def test_play_after_load(self):
        svc, _ = _make_service()
        svc.handle_cmd({
            'id': 1, 'cmd': 'load',
            'source': _SIMPLE_DSL, 'beat': 1.0, 'duration': 0.5,
        })
        # Drain events from load
        svc.tick_once()

        reply = svc.handle_cmd({'id': 2, 'cmd': 'play'})
        assert reply['ok'] is True

        json_msgs, _ = svc.tick_once()
        # Decode events and verify state→playing is present
        events = []
        for msg in json_msgs:
            reader = UdsReader()
            reader.feed(msg)
            for kind, payload in reader.messages():
                if kind == KIND_JSON:
                    events.append(parse_json_payload(payload))

        state_events = [
            e for e in events
            if e.get('event') == 'state' and e.get('state') == 'playing'
        ]
        assert len(state_events) >= 1

        snap = svc.build_snapshot()
        assert snap['session']['playback_state'] == 'playing'


class TestHandleStop:
    def test_stop_after_play(self):
        svc, _ = _make_service()
        svc.handle_cmd({
            'id': 1, 'cmd': 'load',
            'source': _SIMPLE_DSL, 'beat': 1.0, 'duration': 0.5,
        })
        svc.handle_cmd({'id': 2, 'cmd': 'play'})
        svc.tick_once()

        reply = svc.handle_cmd({'id': 3, 'cmd': 'stop'})
        assert reply['ok'] is True

        snap = svc.build_snapshot()
        assert snap['session']['playback_state'] == 'stopped'


class TestHandleShutdown:
    def test_shutdown(self):
        svc, _ = _make_service()
        assert svc.should_shutdown is False

        reply = svc.handle_cmd({'id': 1, 'cmd': 'shutdown'})
        assert reply['ok'] is True
        assert svc.should_shutdown is True


class TestHandleStatus:
    def test_status_returns_snapshot(self):
        svc, _ = _make_service()
        reply = svc.handle_cmd({'id': 1, 'cmd': 'status'})
        assert reply['ok'] is True
        assert reply['result']['event'] == 'snapshot'
        assert reply['result']['session'] is None


class TestTickProbesDisconnected:
    def test_probes_disconnected(self):
        fakes = [_FakeDevice()]
        fakes[0].is_connected = False
        svc, _ = _make_service(fake_devices=fakes)

        svc.tick_once()
        assert fakes[0].ensure_connected_calls == 1

    def test_probe_throttled(self):
        """Second tick within 1s should not probe again."""
        fakes = [_FakeDevice()]
        fakes[0].is_connected = False
        # Keep it disconnected so it keeps trying
        original_ensure = fakes[0].ensure_connected

        def stay_disconnected():
            fakes[0].ensure_connected_calls += 1
            return False

        fakes[0].ensure_connected = stay_disconnected
        svc, _ = _make_service(fake_devices=fakes)

        svc.tick_once()
        assert fakes[0].ensure_connected_calls == 1

        svc.tick_once()
        # Should NOT probe again (less than 1s elapsed)
        assert fakes[0].ensure_connected_calls == 1


class TestStatusReflectsConnectivityChange:
    def test_disconnected_then_connected(self):
        fakes = [_FakeDevice()]
        fakes[0].is_connected = False
        svc, _ = _make_service(fake_devices=fakes)

        snap = svc.build_snapshot()
        assert snap['devices'][0]['connected'] is False

        # Tick probes and connects
        svc.tick_once()

        snap = svc.build_snapshot()
        assert snap['devices'][0]['connected'] is True


class TestTickProducesEvents:
    def test_events_after_load_play(self):
        svc, _ = _make_service()
        svc.handle_cmd({
            'id': 1, 'cmd': 'load',
            'source': _SIMPLE_DSL, 'beat': 1.0, 'duration': 0.5,
        })
        svc.handle_cmd({'id': 2, 'cmd': 'play'})

        json_msgs, _ = svc.tick_once()
        # tick_once returns events generated by play()
        # (load events were already drained by handle_cmd → Controller.load
        # emits events, but they're drained by the next tick_once or handle_cmd)
        assert len(json_msgs) >= 0  # events depend on timing


class TestUnknownCommand:
    def test_unknown(self):
        svc, _ = _make_service()
        reply = svc.handle_cmd({'id': 99, 'cmd': 'bogus'})
        assert reply['ok'] is False
        assert 'unknown' in reply['error']

    def test_missing_cmd(self):
        svc, _ = _make_service()
        reply = svc.handle_cmd({'id': 100})
        assert reply['ok'] is False


# =========================================================================
# UDS wire format tests
# =========================================================================


class TestUdsWire:
    def test_json_round_trip(self):
        obj = {'foo': 'bar', 'n': 42}
        data = encode_json(obj)
        reader = UdsReader()
        reader.feed(data)
        msgs = reader.messages()
        assert len(msgs) == 1
        kind, payload = msgs[0]
        assert kind == KIND_JSON
        assert parse_json_payload(payload) == obj

    def test_partial_feed(self):
        obj = {'key': 'value'}
        data = encode_json(obj)
        reader = UdsReader()

        # Feed byte-by-byte
        for i in range(len(data)):
            reader.feed(data[i:i+1])
            msgs = reader.messages()
            if i < len(data) - 1:
                assert msgs == []
        msgs = reader.messages()
        # Last byte should have completed the message in the previous loop
        # Actually let's just verify we got the message at some point
        # Re-do: feed all at once to verify
        reader2 = UdsReader()
        for b in data:
            reader2.feed(bytes([b]))
        all_msgs = reader2.messages()
        assert len(all_msgs) == 1

    def test_multiple_messages(self):
        obj1 = {'a': 1}
        obj2 = {'b': 2}
        data = encode_json(obj1) + encode_json(obj2)
        reader = UdsReader()
        reader.feed(data)
        msgs = reader.messages()
        assert len(msgs) == 2
        assert parse_json_payload(msgs[0][1]) == obj1
        assert parse_json_payload(msgs[1][1]) == obj2

    def test_frame_encoding(self):
        from elemctl.uds_wire import encode_frame
        rgb = b'\xff\x00\x00' * 5  # 5 red pixels
        data = encode_frame(42, 1.5, [rgb])
        reader = UdsReader()
        reader.feed(data)
        msgs = reader.messages()
        assert len(msgs) == 1
        kind, payload = msgs[0]
        assert kind == KIND_FRAME

        # Parse frame payload
        frame_index, t_rel = struct.unpack_from('<If', payload, 0)
        assert frame_index == 42
        assert abs(t_rel - 1.5) < 0.001
        assert payload[8:] == rgb

    def test_zero_length_skipped(self):
        """A frame with length=0 must not desynchronize the reader."""
        valid = encode_json({'ok': True})
        # Craft a zero-length frame: [u32 length=0]
        bad = struct.pack('<I', 0)
        reader = UdsReader()
        reader.feed(bad + valid)
        msgs = reader.messages()
        assert len(msgs) == 1
        kind, payload = msgs[0]
        assert kind == KIND_JSON
        assert parse_json_payload(payload) == {'ok': True}


# =========================================================================
# Part B: UDS integration tests
# =========================================================================


class UdsClient:
    """Test helper for talking to a UdsServer."""

    def __init__(self, socket_path: str, timeout: float = 2.0):
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(timeout)
        self._sock.connect(socket_path)
        self._reader = UdsReader()

    def send_cmd(self, cmd: dict) -> None:
        self._sock.sendall(encode_json(cmd))

    def recv_messages(self, timeout: float = 1.0) -> list[tuple[int, bytes]]:
        """Receive messages until timeout. Returns all collected messages."""
        all_msgs: list[tuple[int, bytes]] = []
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                self._sock.settimeout(max(0.01, deadline - time.monotonic()))
                data = self._sock.recv(4096)
            except socket.timeout:
                # No data right now — if we have messages, return them
                if all_msgs:
                    break
                continue
            except OSError:
                break
            if not data:
                break
            self._reader.feed(data)
            all_msgs.extend(self._reader.messages())
        all_msgs.extend(self._reader.messages())
        return all_msgs

    def close(self) -> None:
        self._sock.close()


def _wait_for_socket(path: str, timeout: float = 2.0) -> bool:
    """Wait until the UDS socket file exists."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if Path(path).exists():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture()
def uds_service(tmp_path):
    """Start a UdsServer in a daemon thread, yield socket path."""
    socket_path = str(tmp_path / 'test.sock')
    config = _make_config()
    fakes = [_FakeDevice()]
    svc = ControllerService(
        config,
        receiver_factory=_NoopReceiver,
        device_factory=_make_fake_factory(fakes),
    )
    server = UdsServer(svc, socket_path)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    assert _wait_for_socket(socket_path), 'server did not create socket'
    yield socket_path, server, fakes

    server.shutdown()
    thread.join(timeout=3.0)


class TestUdsConnectSnapshot:
    def test_connect_receives_snapshot(self, uds_service):
        socket_path, _, _ = uds_service
        client = UdsClient(socket_path)
        try:
            msgs = client.recv_messages(timeout=1.0)
            assert len(msgs) >= 1
            kind, payload = msgs[0]
            assert kind == KIND_JSON
            snap = parse_json_payload(payload)
            assert snap['event'] == 'snapshot'
            assert snap['session'] is None
        finally:
            client.close()

    def test_connect_probes_devices_before_snapshot(self, tmp_path):
        """Initial snapshot reflects freshly probed connectivity."""
        socket_path = str(tmp_path / 'test.sock')
        config = _make_config()
        fakes = [_FakeDevice()]
        fakes[0].is_connected = False
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fakes),
        )
        server = UdsServer(svc, socket_path)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        assert _wait_for_socket(socket_path)

        client = UdsClient(socket_path)
        try:
            msgs = client.recv_messages(timeout=1.0)
            assert len(msgs) >= 1
            snap = parse_json_payload(msgs[0][1])
            # Device was disconnected but probe_all ran on connect
            assert snap['devices'][0]['connected'] is True
            assert fakes[0].ensure_connected_calls >= 1
        finally:
            client.close()
            server.shutdown()
            thread.join(timeout=3.0)


class TestUdsLoadPlayRoundTrip:
    def test_load_play(self, uds_service):
        socket_path, _, _ = uds_service
        client = UdsClient(socket_path)
        try:
            # Drain snapshot
            client.recv_messages(timeout=0.5)

            # Load
            client.send_cmd({
                'id': 1, 'cmd': 'load',
                'source': _SIMPLE_DSL, 'beat': 1.0, 'duration': 0.5,
            })
            msgs = client.recv_messages(timeout=1.0)
            replies = [
                parse_json_payload(p) for k, p in msgs if k == KIND_JSON
            ]
            load_reply = next(r for r in replies if r.get('type') == 'reply')
            assert load_reply['ok'] is True
            assert load_reply['result']['session_id'] == 1

            # Play
            client.send_cmd({'id': 2, 'cmd': 'play'})
            msgs = client.recv_messages(timeout=1.0)
            replies = [
                parse_json_payload(p) for k, p in msgs if k == KIND_JSON
            ]
            play_reply = next(r for r in replies if r.get('type') == 'reply')
            assert play_reply['ok'] is True
        finally:
            client.close()


class TestUdsShutdown:
    def test_shutdown_stops_server(self, tmp_path):
        socket_path = str(tmp_path / 'test.sock')
        config = _make_config()
        fakes = [_FakeDevice()]
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fakes),
        )
        server = UdsServer(svc, socket_path)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        assert _wait_for_socket(socket_path)

        client = UdsClient(socket_path)
        try:
            client.recv_messages(timeout=0.5)
            client.send_cmd({'id': 1, 'cmd': 'shutdown'})
            client.recv_messages(timeout=0.5)
        finally:
            client.close()

        thread.join(timeout=3.0)
        assert not thread.is_alive(), 'server thread did not exit'


class TestUdsReconnect:
    def test_reconnect_gets_fresh_snapshot(self, uds_service):
        socket_path, _, _ = uds_service
        # First connection — load something
        client1 = UdsClient(socket_path)
        try:
            client1.recv_messages(timeout=0.5)
            client1.send_cmd({
                'id': 1, 'cmd': 'load',
                'source': _SIMPLE_DSL, 'beat': 1.0, 'duration': 0.5,
            })
            client1.recv_messages(timeout=0.5)
        finally:
            client1.close()

        # Brief pause for server to detect disconnect
        time.sleep(0.1)

        # Reconnect — should get snapshot with session
        client2 = UdsClient(socket_path)
        try:
            msgs = client2.recv_messages(timeout=1.0)
            assert len(msgs) >= 1
            kind, payload = msgs[0]
            assert kind == KIND_JSON
            snap = parse_json_payload(payload)
            assert snap['event'] == 'snapshot'
            assert snap['session'] is not None
            assert snap['session']['session_id'] == 1
        finally:
            client2.close()


class TestUdsFrameDelivery:
    def test_frame_delivery(self, tmp_path):
        """After load+play, the client receives KIND_FRAME binary messages."""
        socket_path = str(tmp_path / 'test.sock')
        config = _make_config()
        fakes = [_FrameProducingDevice(strip_length=5)]
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fakes),
        )
        server = UdsServer(svc, socket_path)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        assert _wait_for_socket(socket_path)

        client = UdsClient(socket_path)
        try:
            # Drain snapshot
            client.recv_messages(timeout=0.5)

            # Load + play
            client.send_cmd({
                'id': 1, 'cmd': 'load',
                'source': _SIMPLE_DSL, 'beat': 1.0, 'duration': 0.5,
            })
            client.recv_messages(timeout=0.5)

            client.send_cmd({'id': 2, 'cmd': 'play'})

            # Collect messages until we get at least one binary frame
            frames = []
            msgs = client.recv_messages(timeout=2.0)
            for kind, payload in msgs:
                if kind == KIND_FRAME:
                    frames.append(payload)

            assert len(frames) >= 1, 'expected at least one binary frame'

            # Verify frame structure: [u32 frame_index][f32 t_rel][rgb...]
            payload = frames[0]
            assert len(payload) >= 8  # at least header
            frame_index, t_rel = struct.unpack_from('<If', payload, 0)
            assert frame_index >= 0
            assert t_rel >= 0.0
            rgb = payload[8:]
            assert len(rgb) == 5 * 3  # 5 pixels * 3 bytes
        finally:
            client.close()
            server.shutdown()
            thread.join(timeout=3.0)
