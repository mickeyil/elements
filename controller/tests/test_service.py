"""Tests for ControllerService and UdsServer."""

import json
import logging
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
            self._paused_t_rel = t_rel
            self._state = DeviceState.PAUSED

    def pause(self, now_ns):
        if self._state == DeviceState.PLAYING:
            self._paused_t_rel = (now_ns - self._t0_ns) / 1e9
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
        if self._state == DeviceState.PAUSED:
            return getattr(self, '_paused_t_rel', 0.0)
        return 0.0

    def drain_frames(self):
        return []

    def supports_debug_seek(self):
        return False

    def debug_seek(self, t_rel, now_ns):
        self._paused_t_rel = t_rel
        self._state = DeviceState.PAUSED

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


class _TickCountingDevice(_FakeDevice):
    def __init__(self):
        super().__init__()
        self.tick_calls = 0

    def tick_once(self, now_ns):
        self.tick_calls += 1


def _make_fake_factory(fake_devices: list[_FakeDevice]):
    """Return a factory that yields pre-created _FakeDevice instances in order."""
    idx = iter(range(len(fake_devices)))

    def factory(device_id, host, tcp_port, device_type, strip_length, frame_port, udp_receiver):
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


class _FakeClock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


def _make_service(n_devices=1, fake_devices=None, clock=None):
    config = _make_config(n_devices)
    if fake_devices is None:
        fake_devices = [_FakeDevice() for _ in range(n_devices)]
    kwargs = dict(
        receiver_factory=_NoopReceiver,
        device_factory=_make_fake_factory(fake_devices),
    )
    if clock is not None:
        kwargs['clock'] = clock
    return ControllerService(config, **kwargs), fake_devices


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
        assert snap['devices'][0]['strip'] == 'test'
        assert snap['devices'][0]['length'] == 5
        assert snap['devices'][0]['connected'] is True

    def test_snapshot_shows_disconnected(self):
        fakes = [_FakeDevice()]
        fakes[0].is_connected = False
        svc, _ = _make_service(fake_devices=fakes)
        snap = svc.build_snapshot()
        assert snap['devices'][0]['connected'] is False

    def test_idle_tick_once_still_ticks_devices(self):
        fake = _TickCountingDevice()
        svc, _ = _make_service(fake_devices=[fake])
        svc.tick_once()
        assert fake.tick_calls == 1


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
    def test_play_reply_and_snapshot(self):
        svc, _ = _make_service()
        svc.handle_cmd({
            'id': 1, 'cmd': 'load',
            'source': _SIMPLE_DSL, 'beat': 1.0, 'duration': 0.5,
        })
        svc.tick_once()

        reply = svc.handle_cmd({'id': 2, 'cmd': 'play'})
        assert reply['ok'] is True

        svc.tick_once()
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


class TestPresenceSnapshot:
    def test_snapshot_aggregate_all_connected(self):
        svc, _ = _make_service(n_devices=2)
        snap = svc.build_snapshot()
        assert snap['online_count'] == 2
        assert snap['expected_count'] == 2
        # Consistency: online_count matches per-device connected flags
        assert snap['online_count'] == sum(
            1 for d in snap['devices'] if d['connected']
        )

    def test_snapshot_aggregate_one_disconnected(self):
        fakes = [_FakeDevice(), _FakeDevice()]
        fakes[1].is_connected = False
        svc, _ = _make_service(n_devices=2, fake_devices=fakes)
        snap = svc.build_snapshot()
        assert snap['online_count'] == 1
        assert snap['expected_count'] == 2
        assert snap['online_count'] == sum(
            1 for d in snap['devices'] if d['connected']
        )


def _decode_json_msgs(json_msgs):
    """Decode list of UDS-encoded bytes into dicts."""
    events = []
    for msg in json_msgs:
        reader = UdsReader()
        reader.feed(msg)
        for kind, payload in reader.messages():
            if kind == KIND_JSON:
                events.append(parse_json_payload(payload))
    return events


class TestPresenceEvents:
    def test_offline_to_online_emits_event(self):
        fakes = [_FakeDevice()]
        fakes[0].is_connected = False
        svc, _ = _make_service(fake_devices=fakes)

        # tick_once probes and reconnects (ensure_connected sets is_connected=True)
        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)

        status_events = [e for e in events if e.get('event') == 'device_status']
        assert len(status_events) == 1
        evt = status_events[0]
        assert evt['connected'] is True
        assert evt['device_id'] == 1
        assert evt['device_uid'] == 'sim-1'
        assert evt['strip'] == 'test'
        assert evt['length'] == 5

        # Snapshot stays aligned
        snap = svc.build_snapshot()
        assert snap['online_count'] == snap['expected_count']

    def test_online_to_offline_emits_event(self):
        fakes = [_FakeDevice()]
        fakes[0].is_connected = True
        svc, _ = _make_service(fake_devices=fakes)

        # Device goes offline externally; prevent probe from reconnecting
        fakes[0].is_connected = False
        fakes[0].ensure_connected = lambda: False
        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)

        status_events = [e for e in events if e.get('event') == 'device_status']
        assert len(status_events) == 1
        assert status_events[0]['connected'] is False

        snap = svc.build_snapshot()
        assert snap['online_count'] == 0

    def test_no_event_when_stable(self):
        svc, fakes = _make_service()
        # All connected at init, stays connected
        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)
        assert not any(e.get('event') == 'device_status' for e in events)

        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)
        assert not any(e.get('event') == 'device_status' for e in events)

    def test_event_ordering_controller_first(self):
        fakes = [_FakeDevice()]
        fakes[0].is_connected = False
        svc, _ = _make_service(fake_devices=fakes)

        # Load + play to generate controller events
        svc.handle_cmd({
            'id': 1, 'cmd': 'load',
            'source': _SIMPLE_DSL, 'beat': 1.0, 'duration': 0.5,
        })
        svc.handle_cmd({'id': 2, 'cmd': 'play'})

        # tick_once should drain controller events, then probe, then detect transition
        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)

        # Find positions of controller events vs device_status
        controller_indices = [
            i for i, e in enumerate(events)
            if e.get('event') in ('state', 'session_start')
        ]
        status_indices = [
            i for i, e in enumerate(events)
            if e.get('event') == 'device_status'
        ]
        assert controller_indices, 'expected at least one controller event'
        assert status_indices, 'expected a device_status event'
        assert max(controller_indices) < min(status_indices), (
            'controller events must appear before device_status'
        )


class TestProbeAllBaseline:
    def test_probe_all_does_not_cause_spurious_event(self):
        """probe_all() on client connect must not cause a spurious
        device_status event on the next tick_once()."""
        fakes = [_FakeDevice()]
        fakes[0].is_connected = False
        svc, _ = _make_service(fake_devices=fakes)

        # Simulate what UdsServer does on client connect
        svc.probe_all()

        # Device is now connected; snapshot should reflect that
        snap = svc.build_snapshot()
        assert snap['devices'][0]['connected'] is True

        # Next tick must NOT emit a spurious device_status connected=true
        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)
        status_events = [e for e in events if e.get('event') == 'device_status']
        assert status_events == [], (
            'probe_all() should sync baseline — no spurious transition event'
        )


class TestTickProducesEvents:
    def test_play_emits_state_playing(self):
        svc, _ = _make_service()
        svc.handle_cmd({
            'id': 1, 'cmd': 'load',
            'source': _SIMPLE_DSL, 'beat': 1.0, 'duration': 0.5,
        })
        # Drain load events
        svc.tick_once()

        svc.handle_cmd({'id': 2, 'cmd': 'play'})
        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)

        playing = [
            e for e in events
            if e.get('event') == 'state' and e.get('state') == 'playing'
        ]
        assert len(playing) == 1


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


from .uds_helpers import UdsClient, wait_for_socket as _wait_for_socket


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
    assert not thread.is_alive(), 'server thread did not exit after shutdown'


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
            assert not thread.is_alive(), 'server thread did not exit'


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


# =========================================================================
# Discovery integration tests
# =========================================================================


class _FakeDiscovery:
    """Fake DiscoveryReceiver for service tests."""

    def __init__(self, port):
        self._pending: list[tuple[str, str, int]] = []

    def inject(self, uid: str, host: str, tcp_port: int) -> None:
        self._pending.append((uid, host, tcp_port))

    def poll(self) -> None:
        pass

    def drain_discoveries(self) -> list[tuple[str, str, int]]:
        out = self._pending[:]
        self._pending.clear()
        return out

    def close(self) -> None:
        pass


def _make_discovery_service(n_devices=2, fake_devices=None):
    """Create a ControllerService with discovery enabled."""
    devices = []
    for i in range(n_devices):
        devices.append(DeviceConfig(
            device_id=i + 1,
            device_uid=f'sim-{i + 1}',
            device_type='sim',
            host='',
            tcp_port=0,
            strip_id=f'strip_{chr(ord("a") + i)}',
            length=5,
        ))
    config = Config(frame_port=1, devices=devices, discovery_port=9999)
    if fake_devices is None:
        fake_devices = [_FakeDevice() for _ in range(n_devices)]
        for fd in fake_devices:
            fd.is_connected = False
            fd.ensure_connected = lambda: False
    fake_disc = _FakeDiscovery(9999)
    svc = ControllerService(
        config,
        receiver_factory=_NoopReceiver,
        device_factory=_make_fake_factory(fake_devices),
        discovery_factory=lambda port: fake_disc,
    )
    return svc, fake_devices, fake_disc


class TestDiscoveryIntegration:
    def test_discovery_updates_device_address(self):
        fakes = [_FakeDevice()]
        fakes[0].is_connected = False
        fakes[0].ensure_connected = lambda: False
        address_updates = []
        fakes[0].update_address = lambda h, p: address_updates.append((h, p))

        svc, _, disc = _make_discovery_service(n_devices=1, fake_devices=fakes)
        disc.inject('sim-1', '10.0.0.1', 8001)
        svc.tick_once()
        assert len(address_updates) == 1
        assert address_updates[0] == ('10.0.0.1', 8001)

    def test_discovery_unknown_uid_ignored(self):
        fakes = [_FakeDevice()]
        fakes[0].is_connected = False
        fakes[0].ensure_connected = lambda: False
        fakes[0].update_address = lambda h, p: None

        svc, _, disc = _make_discovery_service(n_devices=1, fake_devices=fakes)
        disc.inject('unknown-device', '10.0.0.1', 8001)
        svc.tick_once()  # Should not raise

    def test_service_survives_unresolved_startup_then_discovery(self):
        """Service ticks safely with unresolved devices; discovery updates address."""
        fakes = [_FakeDevice()]
        fakes[0].is_connected = False
        fakes[0].ensure_connected = lambda: False
        fakes[0]._host = ''
        fakes[0]._tcp_port = 0

        def update_address(host, tcp_port):
            fakes[0]._host = host
            fakes[0]._tcp_port = tcp_port

        fakes[0].update_address = update_address

        svc, _, disc = _make_discovery_service(n_devices=1, fake_devices=fakes)

        # Tick without discovery — service must not crash
        svc.tick_once()

        # Now inject discovery
        disc.inject('sim-1', '127.0.0.1', 9001)
        svc.tick_once()
        assert fakes[0]._host == '127.0.0.1'
        assert fakes[0]._tcp_port == 9001

    def test_discovery_triggers_reconnect(self):
        """Discover same UID with new address triggers address update."""
        fakes = [_FakeDevice()]
        fakes[0].is_connected = True
        fakes[0]._host = '10.0.0.1'
        fakes[0]._tcp_port = 8001
        address_updates = []

        def update_address(host, tcp_port):
            address_updates.append((host, tcp_port))

        fakes[0].update_address = update_address

        svc, _, disc = _make_discovery_service(n_devices=1, fake_devices=fakes)

        # Discover with a different address
        disc.inject('sim-1', '10.0.0.2', 8002)
        svc.tick_once()
        assert len(address_updates) == 1
        assert address_updates[0] == ('10.0.0.2', 8002)

    def test_discovery_clears_probe_throttle(self):
        """After discovery updates address, probe should not be throttled."""
        fakes = [_FakeDevice()]
        fakes[0].is_connected = False
        probe_calls = []
        current_addr = ['', 0]

        def tracking_ensure():
            probe_calls.append(1)
            return False  # stay disconnected

        def tracking_update(h, p):
            if h == current_addr[0] and p == current_addr[1]:
                return False
            current_addr[0] = h
            current_addr[1] = p
            return True

        fakes[0].ensure_connected = tracking_ensure
        fakes[0].update_address = tracking_update

        svc, _, disc = _make_discovery_service(n_devices=1, fake_devices=fakes)

        # First tick probes (1 call), sets throttle
        svc.tick_once()
        assert len(probe_calls) == 1

        # Second tick without discovery — throttled, no probe
        svc.tick_once()
        assert len(probe_calls) == 1

        # Discovery arrives with new address — should clear throttle
        disc.inject('sim-1', '127.0.0.1', 9001)
        svc.tick_once()
        # Probe should run again in the same tick
        assert len(probe_calls) == 2

    def test_repeated_hello_does_not_defeat_throttle(self):
        """Identical HELLOs must not clear probe throttle."""
        fakes = [_FakeDevice()]
        fakes[0].is_connected = False
        probe_calls = []
        current_addr = ['', 0]

        def tracking_ensure():
            probe_calls.append(1)
            return False

        def tracking_update(h, p):
            if h == current_addr[0] and p == current_addr[1]:
                return False
            current_addr[0] = h
            current_addr[1] = p
            return True

        fakes[0].ensure_connected = tracking_ensure
        fakes[0].update_address = tracking_update

        svc, _, disc = _make_discovery_service(n_devices=1, fake_devices=fakes)

        # First HELLO — new address, clears throttle, probes
        disc.inject('sim-1', '127.0.0.1', 9001)
        svc.tick_once()
        assert len(probe_calls) == 1

        # Second identical HELLO — should NOT clear throttle
        disc.inject('sim-1', '127.0.0.1', 9001)
        svc.tick_once()
        assert len(probe_calls) == 1, (
            'repeated identical HELLO should not defeat probe throttle'
        )

    def test_discovery_logs_known_uid_address_change(self, caplog):
        fakes = [_FakeDevice()]
        fakes[0].is_connected = False
        fakes[0].update_address = lambda h, p: True

        svc, _, disc = _make_discovery_service(n_devices=1, fake_devices=fakes)
        disc.inject('sim-1', '127.0.0.1', 9001)

        with caplog.at_level(logging.INFO):
            svc.tick_once()

        assert (
            'discovery: connected to sim-1 at 127.0.0.1:9001 (strip strip_a, 5 LEDs)'
            in caplog.text
        )


class _DebugFakeDevice(_FakeDevice):
    """Fake device that supports debug_seek."""

    def supports_debug_seek(self):
        return True


class TestHandlePause:
    def test_pause_happy_path(self):
        clock = _FakeClock()
        svc, _ = _make_service(clock=clock)
        svc.handle_cmd({
            'id': 1, 'cmd': 'load',
            'source': _SIMPLE_DSL, 'beat': 1.0, 'duration': 0.5,
        })
        svc.handle_cmd({'id': 2, 'cmd': 'play'})
        clock.now += 100_000_000  # 100 ms
        svc.tick_once()

        reply = svc.handle_cmd({'id': 3, 'cmd': 'pause'})
        assert reply['ok'] is True

        snap = svc.build_snapshot()
        assert snap['session']['playback_state'] == 'paused'
        assert snap['session']['current_t_rel'] == pytest.approx(0.1)

    def test_pause_emits_state_event(self):
        clock = _FakeClock()
        svc, _ = _make_service(clock=clock)
        svc.handle_cmd({
            'id': 1, 'cmd': 'load',
            'source': _SIMPLE_DSL, 'beat': 1.0, 'duration': 0.5,
        })
        svc.handle_cmd({'id': 2, 'cmd': 'play'})
        clock.now += 100_000_000
        svc.tick_once()

        svc.handle_cmd({'id': 3, 'cmd': 'pause'})
        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)
        paused = [
            e for e in events
            if e.get('event') == 'state' and e.get('state') == 'paused'
        ]
        assert len(paused) == 1


# DSL with a gap so the compiler produces safe intervals for seek tests
_GAP_DSL = """\
from elements.dsl import strip, spark, sec
s = strip('test', length=5)
sp = spark(color='white', fade=1.0)
sp.schedule(s.pixels('0-4'), at=sec(0.5), duration=sec(0.5))
"""


class TestHandleSeek:
    def test_seek_missing_t_rel(self):
        svc, _ = _make_service()
        reply = svc.handle_cmd({'id': 1, 'cmd': 'seek'})
        assert reply['ok'] is False
        assert 't_rel' in reply['error']

    def test_seek_rejects_bool(self):
        svc, _ = _make_service()
        reply = svc.handle_cmd({'id': 1, 'cmd': 'seek', 't_rel': True})
        assert reply['ok'] is False
        assert 'finite' in reply['error']

    def test_seek_rejects_nan(self):
        svc, _ = _make_service()
        reply = svc.handle_cmd({'id': 1, 'cmd': 'seek', 't_rel': float('nan')})
        assert reply['ok'] is False
        assert 'finite' in reply['error']

    def test_seek_rejects_inf(self):
        svc, _ = _make_service()
        reply = svc.handle_cmd({'id': 1, 'cmd': 'seek', 't_rel': float('inf')})
        assert reply['ok'] is False
        assert 'finite' in reply['error']

    def test_seek_rejects_invalid_string(self):
        svc, _ = _make_service()
        reply = svc.handle_cmd({'id': 1, 'cmd': 'seek', 't_rel': 'abc'})
        assert reply['ok'] is False

    def test_seek_from_loaded_updates_snapshot(self):
        svc, _ = _make_service()
        # _GAP_DSL has safe_interval [0.0, 0.5); seek to 0.75 snaps to 0.0
        svc.handle_cmd({
            'id': 1, 'cmd': 'load',
            'source': _GAP_DSL, 'beat': 1.0, 'duration': 1.0,
        })
        svc.tick_once()

        reply = svc.handle_cmd({'id': 2, 'cmd': 'seek', 't_rel': 0.75})
        assert reply['ok'] is True

        snap = svc.build_snapshot()
        assert snap['session']['playback_state'] == 'paused'
        assert snap['session']['epoch'] == 1  # advanced from initial 0
        assert snap['session']['current_t_rel'] == pytest.approx(0.0)


class TestHandleDebugSeek:
    def test_debug_seek_missing_t_rel(self):
        svc, _ = _make_service()
        reply = svc.handle_cmd({'id': 1, 'cmd': 'debug_seek'})
        assert reply['ok'] is False
        assert 't_rel' in reply['error']

    def test_debug_seek_from_loaded_updates_snapshot(self):
        fakes = [_DebugFakeDevice()]
        svc, _ = _make_service(fake_devices=fakes)
        svc.handle_cmd({
            'id': 1, 'cmd': 'load',
            'source': _SIMPLE_DSL, 'beat': 1.0, 'duration': 0.5,
        })
        svc.tick_once()

        reply = svc.handle_cmd({'id': 2, 'cmd': 'debug_seek', 't_rel': 0.2})
        assert reply['ok'] is True

        snap = svc.build_snapshot()
        assert snap['session']['playback_state'] == 'paused'
        assert snap['session']['current_t_rel'] == pytest.approx(0.2)

    def test_debug_seek_unsupported_emits_error(self):
        svc, _ = _make_service()  # default _FakeDevice, supports_debug_seek=False
        svc.handle_cmd({
            'id': 1, 'cmd': 'load',
            'source': _SIMPLE_DSL, 'beat': 1.0, 'duration': 0.5,
        })
        svc.handle_cmd({'id': 2, 'cmd': 'play'})
        svc.tick_once()

        svc.handle_cmd({'id': 3, 'cmd': 'debug_seek', 't_rel': 0.1})
        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)
        errors = [e for e in events if e.get('event') == 'error']
        assert len(errors) >= 1


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
            assert not thread.is_alive(), 'server thread did not exit'
