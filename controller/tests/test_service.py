"""Tests for ControllerService and ControllerServer."""

import copy
import hashlib
import json
import logging
import socket
import struct
import sys
import threading
import time
import types
from pathlib import Path

import pytest

_repo = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo / 'compiler'))

from elemctl.config import Config, DeviceConfig, MAX_DEVICE_PIXELS, load_config, load_config_obj
from elemctl.clock_sync import ClockSyncPollResult, SyncUpdate
from elemctl.controller import ControllerState
from elemctl.device import DeviceState
from elemctl.library import ProgramEntry
from elemctl.program_metadata import extract_metadata, extract_strips
from elemctl.service import ControllerService
from elemctl.server import ControllerServer
from elemctl.controller_protocol import (
    KIND_FRAME,
    KIND_JSON,
    PROTOCOL_VERSION,
    ROLE_OBSERVER,
    ProtocolReader,
    encode_json,
    parse_json_payload,
)
from elements.types import CompiledManifest, CompiledStripArtifact


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
        self._activity_observed = False
        self._host = ''
        self._tcp_port = 0
        self.is_connected = True
        self.ensure_connected_calls = 0
        self.load_calls = 0
        self.start_calls = 0
        self.jump_calls = 0
        self.pause_calls = 0
        self.resume_calls = 0
        self.stop_calls = 0
        self.close_calls = 0
        self.reboot_calls = 0
        self.sync_results: list[tuple[int, int, int]] = []
        self.store_background_calls: list[tuple[bytes, int, int]] = []
        self.clear_background_calls = 0
        self.store_background_ok = True
        self.clear_background_ok = True
        self.query_device_status_calls = 0
        self.reported_status = {
            'mode': 'attached_controlled',
            'profile_present': True,
            'profile_strip_length': 10,
            'background_present': False,
            'background_strip_length': 0,
            'background_blob_len': 0,
            'background_crc32': 0,
        }

    def load(self, blob, gen):
        self.load_calls += 1
        self._state = DeviceState.LOADED
        return True

    def start(self, t0_ns):
        self.start_calls += 1
        self._t0_ns = t0_ns
        self._state = DeviceState.PLAYING

    def jump(self, t0_ns, t_rel, gen):
        self.jump_calls += 1
        self._t0_ns = t0_ns
        if self._state != DeviceState.PLAYING:
            self._paused_t_rel = t_rel
            self._state = DeviceState.PAUSED

    def pause(self, now_ns):
        self.pause_calls += 1
        if self._state == DeviceState.PLAYING:
            self._paused_t_rel = (now_ns - self._t0_ns) / 1e9
        self._state = DeviceState.PAUSED

    def resume(self, t0_ns):
        self.resume_calls += 1
        self._t0_ns = t0_ns
        self._state = DeviceState.PLAYING

    def stop(self):
        self.stop_calls += 1
        self._state = DeviceState.LOADED

    def reboot(self):
        self.reboot_calls += 1
        self.is_connected = False
        return True

    def send_sync_result(self, seq: int, boot_token: int, offset_us: int):
        self.sync_results.append((seq, boot_token, offset_us))
        return True

    def store_background(self, blob: bytes, strip_length: int, crc32: int) -> bool:
        self.store_background_calls.append((bytes(blob), strip_length, crc32))
        return self.store_background_ok

    def clear_background(self) -> bool:
        self.clear_background_calls += 1
        return self.clear_background_ok

    def query_device_status(self):
        self.query_device_status_calls += 1
        return copy.deepcopy(self.reported_status)

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

    def produces_program_frames(self):
        return False

    def supports_debug_seek(self):
        return False

    def debug_seek(self, t_rel, now_ns):
        self._paused_t_rel = t_rel
        self._state = DeviceState.PAUSED

    def ensure_connected(self):
        self.ensure_connected_calls += 1
        self.is_connected = True
        return True

    def update_address(self, host, tcp_port):
        changed = host != self._host or tcp_port != self._tcp_port
        self._host = host
        self._tcp_port = tcp_port
        return changed

    def consume_activity_observed(self):
        seen = self._activity_observed
        self._activity_observed = False
        return seen

    def disconnect_transport(self):
        self._activity_observed = False
        self.is_connected = False

    def close(self):
        self.close_calls += 1
        self.is_connected = False
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
        self._activity_observed = True
        self._frame_index += 1

    def drain_frames(self):
        out = self._pending_frames[:]
        self._pending_frames.clear()
        return out

    def produces_program_frames(self):
        return True


class _TickCountingDevice(_FakeDevice):
    def __init__(self):
        super().__init__()
        self.tick_calls = 0

    def tick_once(self, now_ns):
        self.tick_calls += 1


class _AddressableFakeDevice(_FakeDevice):
    def __init__(self):
        super().__init__()
        self._host = ''
        self._tcp_port = 0

    def update_address(self, host, tcp_port):
        changed = host != self._host or tcp_port != self._tcp_port
        self._host = host
        self._tcp_port = tcp_port
        return changed

    def close(self):
        super().close()


class _FakeLibrary:
    def __init__(self, animations_dir: str, entries: list[ProgramEntry] | None = None):
        self.animations_dir = animations_dir
        self._entries = list(entries or [])
        self.rescan_calls = 0
        self.publish_calls: list[tuple[str, str]] = []

    def list_programs(self) -> list[ProgramEntry]:
        return sorted(self._entries, key=lambda entry: entry.program_id)

    def get(self, program_id: str) -> ProgramEntry | None:
        for entry in self._entries:
            if entry.program_id == program_id:
                return entry
        return None

    def rescan(self) -> list[ProgramEntry]:
        self.rescan_calls += 1
        return self.list_programs()

    def publish(self, program_id: str, source: str) -> ProgramEntry:
        self.publish_calls.append((program_id, source))
        source_hash = hashlib.sha256(source.encode('utf-8')).hexdigest()
        try:
            beat, duration = extract_metadata(source, f'/tmp/{program_id}.py')
        except ValueError as e:
            raise ValueError(str(e)) from e

        entry = ProgramEntry(
            program_id=program_id,
            path=f'/tmp/{program_id}.py',
            source=source,
            source_hash=source_hash,
            beat=beat,
            duration=duration,
            error=None,
            strips=extract_strips(source, f'/tmp/{program_id}.py'),
        )

        self._entries = [
            existing for existing in self._entries
            if existing.program_id != program_id
        ]
        self._entries.append(entry)
        return entry


class _FakeClockSyncManager:
    def __init__(self):
        self.connected_ids: list[int] = []
        self.disconnected_ids: list[int] = []
        self.pruned_to: set[int] | None = None
        self.targets_sent: list[list[tuple[int, str]]] = []
        self.status_by_device: dict[int, dict[str, object]] = {}
        self.pending_updates: list = []
        self.pending_activity_ids: set[int] = set()
        self.closed = False

    def close(self):
        self.closed = True

    def prune_device_ids(self, valid_ids: set[int]):
        self.pruned_to = set(valid_ids)

    def on_connected(self, device_id: int):
        self.connected_ids.append(device_id)

    def on_disconnected(self, device_id: int):
        self.disconnected_ids.append(device_id)

    def clock_status(self, device_id: int, now_ns: int):
        _ = now_ns
        return self.status_by_device.get(device_id, {
            'clock_state': 'pending',
            'clock_offset_ms': None,
            'clock_rtt_ms': None,
            'clock_last_sync_age_s': None,
        })

    def poll(self):
        out = ClockSyncPollResult(
            status_updates=list(self.pending_updates),
            observed_activity_device_ids=set(self.pending_activity_ids),
        )
        self.pending_updates.clear()
        self.pending_activity_ids.clear()
        return out

    def send_due_probes(self, targets, now_ns):
        _ = now_ns
        self.targets_sent.append(list(targets))


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

_MAIN_144_DSL = """\
from elements.dsl import strip, spark, sec
s = strip('main', length=144)
sp = spark(color='white', fade=1.0)
sp.schedule(s.pixels('0-143'), at=0, duration=sec(0.5))
"""

_LEFT_RIGHT_DSL = """\
from elements.dsl import strip, spark, sec
left = strip('left', length=5)
right = strip('right', length=5)
sp = spark(color='white', fade=1.0)
sp.schedule(left.pixels('0-4'), at=0, duration=sec(0.5))
sp.schedule(right.pixels('0-4'), at=0, duration=sec(0.5))
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


def _make_mirrored_main_config() -> Config:
    return Config(frame_port=1, devices=[
        DeviceConfig(
            device_id=1,
            device_uid='sim-144',
            device_type='sim',
            host='127.0.0.1',
            tcp_port=9001,
            strip_id='main',
            length=144,
        ),
        DeviceConfig(
            device_id=2,
            device_uid='esp-144',
            device_type='esp32',
            host='127.0.0.1',
            tcp_port=9002,
            strip_id='main',
            length=144,
        ),
    ])


def _make_mirrored_lr_config() -> Config:
    return Config(frame_port=1, devices=[
        DeviceConfig(1, 'sim-left', 'sim', '127.0.0.1', 9001, 'left', 5),
        DeviceConfig(2, 'esp-left', 'esp32', '127.0.0.1', 9002, 'left', 5),
        DeviceConfig(3, 'sim-right', 'sim', '127.0.0.1', 9003, 'right', 5),
        DeviceConfig(4, 'esp-right', 'esp32', '127.0.0.1', 9004, 'right', 5),
    ])


def _make_program_entry(
    program_id: str,
    *,
    source: str = _SIMPLE_DSL,
    beat: float = 1.0,
    duration: float = 0.5,
    error: str | None = None,
    strips: list[str] | None = None,
) -> ProgramEntry:
    source_hash = None if source is None else hashlib.sha256(source.encode('utf-8')).hexdigest()
    return ProgramEntry(
        program_id=program_id,
        path=f'/tmp/{program_id}.py',
        source=source,
        source_hash=source_hash,
        beat=None if error is not None else beat,
        duration=None if error is not None else duration,
        error=error,
        strips=None if error is not None else strips,
    )


class _FakeClock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


class _FakeWallClock:
    def __init__(self, initial: float = 0.0):
        self.now = initial

    def __call__(self):
        return self.now


def _make_service(
    n_devices=1,
    fake_devices=None,
    clock=None,
    wall_clock=None,
    library_factory=None,
    clock_sync_factory=None,
):
    config = _make_config(n_devices)
    if fake_devices is None:
        fake_devices = [_FakeDevice() for _ in range(n_devices)]
    if library_factory is None:
        library_factory = lambda animations_dir: _FakeLibrary(animations_dir)
    kwargs = dict(
        receiver_factory=_NoopReceiver,
        device_factory=_make_fake_factory(fake_devices),
        library_factory=library_factory,
    )
    if clock_sync_factory is not None:
        kwargs['clock_sync_factory'] = clock_sync_factory
    if clock is not None:
        kwargs['clock'] = clock
    if wall_clock is not None:
        kwargs['wall_clock'] = wall_clock
    return ControllerService(config, **kwargs), fake_devices


def _config_to_doc(config: Config) -> dict:
    controller = {
        'frame_port': config.frame_port,
        'discovery_port': config.discovery_port,
    }
    if config.animations_dir is not None:
        controller['animations_dir'] = config.animations_dir
    if config.logs_dir is not None:
        controller['logs_dir'] = config.logs_dir
    return {
        'controller': controller,
        'devices': [
            {
                'device_id': dc.device_id,
                'device_uid': dc.device_uid,
                'device_type': dc.device_type,
                'host': dc.host,
                'tcp_port': dc.tcp_port,
                'strip_id': dc.strip_id,
                'length': dc.length,
            }
            for dc in config.devices
        ],
    }


def _make_service_with_path(
    tmp_path,
    config: Config,
    *,
    device_factory,
    discovery_factory=None,
    library_factory=None,
):
    path = tmp_path / 'config.json'
    path.write_text(json.dumps(_config_to_doc(config)))
    resolved_config = load_config(str(path))
    if library_factory is None:
        library_factory = lambda animations_dir: _FakeLibrary(animations_dir)
    kwargs = {
        'config_path': str(path),
        'receiver_factory': _NoopReceiver,
        'device_factory': device_factory,
        'library_factory': library_factory,
    }
    if discovery_factory is not None:
        kwargs['discovery_factory'] = discovery_factory
    svc = ControllerService(resolved_config, **kwargs)
    return svc, path


# =========================================================================
# Part A: ControllerService unit tests
# =========================================================================


class TestSnapshotIdle:
    def test_snapshot_idle(self):
        svc, _ = _make_service()
        snap = svc.build_snapshot()

        assert snap['type'] == 'event'
        assert snap['event'] == 'snapshot'
        assert snap['protocol_version'] == PROTOCOL_VERSION
        assert snap['session'] is None
        assert len(snap['devices']) == 1
        assert snap['devices'][0]['device_id'] == 1
        assert snap['devices'][0]['strip'] == 'test'
        assert snap['devices'][0]['length'] == 5
        assert snap['devices'][0]['connected'] is True

    def test_snapshot_last_seen_is_none_for_never_connected_device(self):
        fakes = [_FakeDevice()]
        fakes[0].is_connected = False
        wall_clock = _FakeWallClock(123.5)
        svc, _ = _make_service(fake_devices=fakes, wall_clock=wall_clock)

        snap = svc.build_snapshot()

        assert snap['devices'][0]['connected'] is False
        assert snap['devices'][0]['last_seen'] is None

    def test_snapshot_shows_disconnected(self):
        fakes = [_FakeDevice()]
        fakes[0].is_connected = False
        svc, _ = _make_service(fake_devices=fakes)
        snap = svc.build_snapshot()
        assert snap['devices'][0]['connected'] is False

    def test_snapshot_includes_clock_status_defaults(self):
        config = Config(frame_port=1, devices=[
            DeviceConfig(1, 'sim-left', 'sim', '127.0.0.1', 9001, 'left', 5),
            DeviceConfig(2, 'esp-left', 'esp32', '127.0.0.1', 9002, 'left', 5),
        ])
        fake_devices = [_FakeDevice(), _FakeDevice()]
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fake_devices),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir),
        )

        snap = svc.build_snapshot()

        assert snap['devices'][0]['clock_state'] == 'host'
        assert snap['devices'][0]['clock_offset_ms'] == pytest.approx(0.0)
        assert snap['devices'][0]['clock_rtt_ms'] == pytest.approx(0.0)
        assert snap['devices'][0]['clock_last_sync_age_s'] == pytest.approx(0.0)

        assert snap['devices'][1]['clock_state'] == 'pending'
        assert snap['devices'][1]['clock_offset_ms'] is None
        assert snap['devices'][1]['clock_rtt_ms'] is None
        assert snap['devices'][1]['clock_last_sync_age_s'] is None

    def test_snapshot_uses_sync_manager_state_for_esp32(self):
        config = Config(frame_port=1, devices=[
            DeviceConfig(1, 'esp-left', 'esp32', '127.0.0.1', 9001, 'left', 5),
        ])
        fake_sync = _FakeClockSyncManager()
        fake_sync.status_by_device[1] = {
            'clock_state': 'synced',
            'clock_offset_ms': 2.25,
            'clock_rtt_ms': 1.5,
            'clock_last_sync_age_s': 0.75,
        }
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory([_FakeDevice()]),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir),
            clock_sync_factory=lambda port, clock_ns: fake_sync,
        )

        snap = svc.build_snapshot()

        assert snap['devices'][0]['clock_state'] == 'synced'
        assert snap['devices'][0]['clock_offset_ms'] == pytest.approx(2.25)
        assert snap['devices'][0]['clock_rtt_ms'] == pytest.approx(1.5)
        assert snap['devices'][0]['clock_last_sync_age_s'] == pytest.approx(0.75)

    def test_idle_tick_once_still_ticks_devices(self):
        fake = _TickCountingDevice()
        svc, _ = _make_service(fake_devices=[fake])
        svc.tick_once()
        assert fake.tick_calls == 1

    def test_snapshot_allows_duplicate_strip_ids_with_same_length(self):
        config = Config(frame_port=1, devices=[
            DeviceConfig(
                device_id=1,
                device_uid='sim-144',
                device_type='sim',
                host='127.0.0.1',
                tcp_port=9001,
                strip_id='main',
                length=144,
            ),
            DeviceConfig(
                device_id=2,
                device_uid='esp-144',
                device_type='esp32',
                host='127.0.0.1',
                tcp_port=9002,
                strip_id='main',
                length=144,
            ),
        ])
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory([_FakeDevice(), _FakeDevice()]),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir),
        )

        snap = svc.build_snapshot()

        assert snap['session'] is None
        assert snap['expected_count'] == 2
        assert [d['device_uid'] for d in snap['devices']] == ['sim-144', 'esp-144']
        assert [d['strip'] for d in snap['devices']] == ['main', 'main']
        assert [d['length'] for d in snap['devices']] == [144, 144]


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

    def test_load_resolves_implicit_strip_length_from_topology(self):
        svc, _ = _make_service()
        source = """\
from elements.dsl import strip, spark, sec
s = strip('test')
sp = spark(color='white', fade=1.0)
sp.schedule(s.pixels(f'0-{s.length - 1}'), at=0, duration=sec(0.5))
"""
        reply = svc.handle_cmd({
            'id': 11,
            'cmd': 'load',
            'source': source,
            'beat': 1.0,
            'duration': 0.5,
        })
        assert reply['ok'] is True
        assert reply['result']['session_id'] == 1

    def test_load_allows_program_shorter_than_configured_strip(self):
        svc, _ = _make_service()
        source = """\
from elements.dsl import strip, spark, sec
s = strip('test', length=3)
sp = spark(color='white', fade=1.0)
sp.schedule(s.pixels('0-2'), at=0, duration=sec(0.5))
"""
        reply = svc.handle_cmd({
            'id': 12,
            'cmd': 'load',
            'source': source,
            'beat': 1.0,
            'duration': 0.5,
        })
        assert reply['ok'] is True
        assert reply['result']['session_id'] == 1

    def test_load_rejects_program_longer_than_configured_strip(self):
        svc, _ = _make_service()
        source = """\
from elements.dsl import strip, spark, sec
s = strip('test', length=6)
sp = spark(color='white', fade=1.0)
sp.schedule(s.pixels('0-5'), at=0, duration=sec(0.5))
"""
        reply = svc.handle_cmd({
            'id': 13,
            'cmd': 'load',
            'source': source,
            'beat': 1.0,
            'duration': 0.5,
        })
        assert reply['ok'] is False
        assert 'exceeds configured length' in reply['error']

    def test_load_rejected_on_duplicate_strip_id_topology(self):
        config = Config(frame_port=1, devices=[
            DeviceConfig(1, 'sim-144', 'sim', '127.0.0.1', 9001, 'main', 144),
            DeviceConfig(2, 'esp-144', 'esp32', '127.0.0.1', 9002, 'main', 144),
        ])
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory([_FakeDevice(), _FakeDevice()]),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir),
        )

        reply = svc.handle_cmd({
            'id': 14,
            'cmd': 'load',
            'source': """\
from elements.dsl import strip, spark, sec
s = strip('main', length=144)
sp = spark(color='white', fade=1.0)
sp.schedule(s.pixels('0-143'), at=0, duration=sec(0.5))
""",
            'beat': 1.0,
            'duration': 0.5,
        })

        assert reply['ok'] is False
        assert reply['error'] == 'duplicate strip_id topology requires targeted load support'

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


class TestProgramLibraryCommands:
    def test_snapshot_includes_programs(self):
        entries = [
            _make_program_entry('zeta'),
            _make_program_entry('alpha'),
        ]
        svc, _ = _make_service(
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, entries),
        )

        snap = svc.build_snapshot()

        assert snap['programs'] == [
            {'program_id': 'alpha', 'beat': 1.0, 'duration': 0.5, 'error': None},
            {'program_id': 'zeta', 'beat': 1.0, 'duration': 0.5, 'error': None},
        ]

    def test_snapshot_includes_program_strip_summaries(self):
        entries = [
            _make_program_entry('ambient', strips=['main']),
            _make_program_entry('duo_show', strips=['left', 'right']),
        ]
        svc, _ = _make_service(
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, entries),
        )

        snap = svc.build_snapshot()

        assert snap['programs'] == [
            {
                'program_id': 'ambient',
                'beat': 1.0,
                'duration': 0.5,
                'error': None,
                'strips': ['main'],
            },
            {
                'program_id': 'duo_show',
                'beat': 1.0,
                'duration': 0.5,
                'error': None,
                'strips': ['left', 'right'],
            },
        ]

    def test_rescan_programs_returns_catalog_and_broadcasts_event(self):
        entries = [_make_program_entry('alpha')]
        fake_library = _FakeLibrary('/tmp/programs', entries)
        svc, _ = _make_service(
            library_factory=lambda animations_dir: fake_library,
        )

        reply = svc.handle_cmd({'id': 1, 'cmd': 'rescan_programs'})

        assert reply['ok'] is True
        assert reply['result']['programs'] == [
            {'program_id': 'alpha', 'beat': 1.0, 'duration': 0.5, 'error': None},
        ]
        assert fake_library.rescan_calls == 1

        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)
        assert {
            'type': 'event',
            'event': 'programs_updated',
            'programs': [
                {'program_id': 'alpha', 'beat': 1.0, 'duration': 0.5, 'error': None},
            ],
        } in events

    def test_rescan_programs_includes_strip_summaries(self):
        entries = [_make_program_entry('alpha', strips=['main'])]
        fake_library = _FakeLibrary('/tmp/programs', entries)
        svc, _ = _make_service(
            library_factory=lambda animations_dir: fake_library,
        )

        reply = svc.handle_cmd({'id': 1, 'cmd': 'rescan_programs'})

        assert reply['ok'] is True
        assert reply['result']['programs'] == [
            {
                'program_id': 'alpha',
                'beat': 1.0,
                'duration': 0.5,
                'error': None,
                'strips': ['main'],
            },
        ]

        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)
        assert {
            'type': 'event',
            'event': 'programs_updated',
            'programs': [
                {
                    'program_id': 'alpha',
                    'beat': 1.0,
                    'duration': 0.5,
                    'error': None,
                    'strips': ['main'],
                },
            ],
        } in events


class TestLogicalTopologyNormalization:
    def test_logical_strip_lengths_collapse_mirrored_group(self):
        svc = ControllerService(
            _make_mirrored_main_config(),
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory([_FakeDevice(), _FakeDevice()]),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir),
        )

        assert svc._logical_strip_lengths() == {'main': 144}

    def test_compile_resolves_implicit_strip_length_from_mirrored_topology(self):
        svc = ControllerService(
            _make_mirrored_main_config(),
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory([_FakeDevice(), _FakeDevice()]),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir),
        )

        manifest = svc._compile("""\
from elements.dsl import strip, spark, sec
s = strip('main')
sp = spark(color='white', fade=1.0)
sp.schedule(s.pixels(f'0-{s.length - 1}'), at=0, duration=sec(0.5))
""", 1.0, 0.5)

        assert len(manifest.strips) == 1
        assert manifest.strips[0].strip_id == 'main'
        assert manifest.strips[0].length == 144

    def test_topology_fingerprint_ignores_mirrored_duplicate_group(self):
        single = ControllerService(
            Config(frame_port=1, devices=[
                DeviceConfig(1, 'sim-144', 'sim', '127.0.0.1', 9001, 'main', 144),
            ]),
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory([_FakeDevice()]),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir),
        )
        mirrored = ControllerService(
            _make_mirrored_main_config(),
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory([_FakeDevice(), _FakeDevice()]),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir),
        )

        assert single._topology_fingerprint() == mirrored._topology_fingerprint()

    def test_topology_fingerprint_changes_when_logical_topology_changes(self):
        base = ControllerService(
            Config(frame_port=1, devices=[
                DeviceConfig(1, 'sim-144', 'sim', '127.0.0.1', 9001, 'main', 144),
            ]),
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory([_FakeDevice()]),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir),
        )
        changed_length = ControllerService(
            Config(frame_port=1, devices=[
                DeviceConfig(1, 'sim-100', 'sim', '127.0.0.1', 9001, 'main', 100),
            ]),
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory([_FakeDevice()]),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir),
        )
        changed_name = ControllerService(
            Config(frame_port=1, devices=[
                DeviceConfig(1, 'sim-left', 'sim', '127.0.0.1', 9001, 'left', 144),
            ]),
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory([_FakeDevice()]),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir),
        )

        assert base._topology_fingerprint() != changed_length._topology_fingerprint()
        assert base._topology_fingerprint() != changed_name._topology_fingerprint()

    def test_publish_program_returns_entry_and_broadcasts_event(self):
        fake_library = _FakeLibrary('/tmp/programs')
        svc, _ = _make_service(
            library_factory=lambda animations_dir: fake_library,
        )
        source = "BEAT = 1.0\nDURATION = 8.0\n"

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'publish_program',
            'program_id': 'ambient',
            'source': source,
        })

        assert reply['ok'] is True
        assert reply['result'] == {
            'program': {
                'program_id': 'ambient',
                'beat': 1.0,
                'duration': 8.0,
                'error': None,
            }
        }
        assert fake_library.publish_calls == [('ambient', source)]

        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)
        assert {
            'type': 'event',
            'event': 'programs_updated',
            'programs': [
                {'program_id': 'ambient', 'beat': 1.0, 'duration': 8.0, 'error': None},
            ],
        } in events

    def test_publish_program_broken_source_is_rejected(self):
        fake_library = _FakeLibrary('/tmp/programs')
        svc, _ = _make_service(
            library_factory=lambda animations_dir: fake_library,
        )

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'publish_program',
            'program_id': 'broken',
            'source': "BEAT = 1.0\n",
        })

        assert reply['ok'] is False
        assert reply['error'] == 'missing DURATION'
        assert fake_library.list_programs() == []

        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)
        assert not any(event.get('event') == 'programs_updated' for event in events)

    def test_publish_program_does_not_change_current_session(self):
        fake_library = _FakeLibrary('/tmp/programs', [_make_program_entry('main_show')])
        svc, _ = _make_service(
            library_factory=lambda animations_dir: fake_library,
        )

        load_reply = svc.handle_cmd({'id': 1, 'cmd': 'load_program', 'program_id': 'main_show'})
        assert load_reply['ok'] is True
        before = svc.build_snapshot()['session']
        assert before is not None

        publish_reply = svc.handle_cmd({
            'id': 2,
            'cmd': 'publish_program',
            'program_id': 'main_show',
            'source': "BEAT = 1.0\nDURATION = 1.0\n",
        })
        assert publish_reply['ok'] is True

        after = svc.build_snapshot()['session']
        assert after == before

    def test_load_program_valid(self):
        entry = _make_program_entry('main_show')
        svc, _ = _make_service(
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )

        reply = svc.handle_cmd({'id': 1, 'cmd': 'load_program', 'program_id': 'main_show'})

        assert reply['ok'] is True
        assert reply['result']['session_id'] == 1

        snap = svc.build_snapshot()
        assert snap['session']['strips'] == [
            {
                'name': 'test',
                'length': 5,
                'targets': [
                    {
                        'device_id': 1,
                        'device_uid': 'sim-1',
                        'device_type': 'sim',
                        'length': 5,
                        'session_role': 'serving',
                    }
                ],
            }
        ]

    def test_load_program_resolves_implicit_strip_length_from_topology(self):
        source = """\
from elements.dsl import strip, spark, sec
s = strip('test')
sp = spark(color='white', fade=1.0)
sp.schedule(s.pixels(f'0-{s.length - 1}'), at=0, duration=sec(0.5))
"""
        entry = _make_program_entry('main_show', source=source)
        svc, _ = _make_service(
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )

        reply = svc.handle_cmd({'id': 11, 'cmd': 'load_program', 'program_id': 'main_show'})

        assert reply['ok'] is True
        assert reply['result']['session_id'] == 1

    def test_load_program_unknown(self):
        svc, _ = _make_service()

        reply = svc.handle_cmd({'id': 1, 'cmd': 'load_program', 'program_id': 'missing'})

        assert reply['ok'] is False
        assert reply['error'] == 'program not found: missing'

    def test_load_program_errored_entry(self):
        entry = _make_program_entry('broken', error='missing BEAT')
        svc, _ = _make_service(
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )

        reply = svc.handle_cmd({'id': 1, 'cmd': 'load_program', 'program_id': 'broken'})

        assert reply['ok'] is False
        assert reply['error'] == 'program broken is not loadable: missing BEAT'

    def test_load_program_mirrored_topology_without_targets_loads_all_matching_devices(self):
        entry = _make_program_entry('main_show', source=_MAIN_144_DSL)
        fake_devices = [_FakeDevice(), _FakeDevice()]
        svc = ControllerService(
            _make_mirrored_main_config(),
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fake_devices),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )

        reply = svc.handle_cmd({'id': 16, 'cmd': 'load_program', 'program_id': 'main_show'})

        assert reply['ok'] is True
        assert fake_devices[0].load_calls == 1
        assert fake_devices[1].load_calls == 1

        snap = svc.build_snapshot()
        assert snap['session']['strips'] == [
            {
                'name': 'main',
                'length': 144,
                'targets': [
                    {'device_id': 1, 'device_uid': 'sim-144', 'device_type': 'sim', 'length': 144, 'session_role': 'serving'},
                    {'device_id': 2, 'device_uid': 'esp-144', 'device_type': 'esp32', 'length': 144, 'session_role': 'serving'},
                ],
            }
        ]

        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)
        session_start = next(e for e in events if e.get('event') == 'session_start')
        assert session_start['strips'] == [
            {
                'name': 'main',
                'length': 144,
                'targets': [
                    {'device_id': 1, 'device_uid': 'sim-144', 'device_type': 'sim', 'length': 144, 'session_role': 'serving'},
                    {'device_id': 2, 'device_uid': 'esp-144', 'device_type': 'esp32', 'length': 144, 'session_role': 'serving'},
                ],
            }
        ]

    def test_load_program_targets_subset_leaves_unselected_device_idle(self):
        entry = _make_program_entry('main_show', source=_MAIN_144_DSL)
        config = Config(frame_port=1, devices=[
            DeviceConfig(1, 'sim-144', 'sim', '127.0.0.1', 9001, 'main', 144),
            DeviceConfig(2, 'esp-144', 'esp32', '127.0.0.1', 9002, 'main', 144),
            DeviceConfig(3, 'sim-bench', 'sim', '127.0.0.1', 9003, 'bench', 144),
        ])
        fake_devices = [_FakeDevice(), _FakeDevice(), _FakeDevice()]
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fake_devices),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )

        reply = svc.handle_cmd({
            'id': 17,
            'cmd': 'load_program',
            'program_id': 'main_show',
            'targets': ['sim-144', 'esp-144'],
        })

        assert reply['ok'] is True
        assert fake_devices[0].load_calls == 1
        assert fake_devices[1].load_calls == 1
        assert fake_devices[2].load_calls == 0

        svc.handle_cmd({'id': 18, 'cmd': 'play'})
        svc.handle_cmd({'id': 19, 'cmd': 'stop'})

        assert fake_devices[0].start_calls == 1
        assert fake_devices[1].start_calls == 1
        assert fake_devices[2].start_calls == 0
        assert fake_devices[2].stop_calls == 0

    def test_load_program_targets_unknown_target_rejected(self):
        entry = _make_program_entry('main_show', source=_MAIN_144_DSL)
        fake_devices = [_FakeDevice(), _FakeDevice()]
        svc = ControllerService(
            _make_mirrored_main_config(),
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fake_devices),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )

        reply = svc.handle_cmd({
            'id': 20,
            'cmd': 'load_program',
            'program_id': 'main_show',
            'targets': ['missing'],
        })

        assert reply['ok'] is False
        assert reply['error'] == 'target not found: missing'

    def test_load_program_targets_duplicate_target_rejected(self):
        entry = _make_program_entry('main_show', source=_MAIN_144_DSL)
        fake_devices = [_FakeDevice(), _FakeDevice()]
        svc = ControllerService(
            _make_mirrored_main_config(),
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fake_devices),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )

        reply = svc.handle_cmd({
            'id': 21,
            'cmd': 'load_program',
            'program_id': 'main_show',
            'targets': ['sim-144', 'sim-144'],
        })

        assert reply['ok'] is False
        assert reply['error'] == 'duplicate target: sim-144'

    def test_load_program_targets_missing_strip_coverage_rejected(self):
        entry = _make_program_entry('duo_show', source=_LEFT_RIGHT_DSL)
        fake_devices = [_FakeDevice(), _FakeDevice(), _FakeDevice(), _FakeDevice()]
        svc = ControllerService(
            _make_mirrored_lr_config(),
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fake_devices),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )

        reply = svc.handle_cmd({
            'id': 22,
            'cmd': 'load_program',
            'program_id': 'duo_show',
            'targets': ['sim-left', 'esp-left'],
        })

        assert reply['ok'] is False
        assert reply['error'] == 'missing targets for strip: right'

    def test_load_program_targets_unused_target_rejected(self):
        entry = _make_program_entry('main_show', source=_MAIN_144_DSL)
        config = Config(frame_port=1, devices=[
            DeviceConfig(1, 'sim-144', 'sim', '127.0.0.1', 9001, 'main', 144),
            DeviceConfig(2, 'sim-bench', 'sim', '127.0.0.1', 9002, 'bench', 144),
        ])
        fake_devices = [_FakeDevice(), _FakeDevice()]
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fake_devices),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )

        reply = svc.handle_cmd({
            'id': 23,
            'cmd': 'load_program',
            'program_id': 'main_show',
            'targets': ['sim-144', 'sim-bench'],
        })

        assert reply['ok'] is False
        assert reply['error'] == 'unused target: sim-bench'

    def test_load_program_targets_shorter_target_rejected(self):
        entry = _make_program_entry('main_show', source=_MAIN_144_DSL)
        config = Config(frame_port=1, devices=[
            DeviceConfig(1, 'sim-100', 'sim', '127.0.0.1', 9001, 'main', 100),
        ])
        fake_devices = [_FakeDevice()]
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fake_devices),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )

        reply = svc.handle_cmd({
            'id': 24,
            'cmd': 'load_program',
            'program_id': 'main_show',
            'targets': ['sim-100'],
        })

        assert reply['ok'] is False
        assert 'exceeds configured length' in reply['error']

    def test_load_program_targets_multi_strip_mirror_succeeds(self):
        entry = _make_program_entry('duo_show', source=_LEFT_RIGHT_DSL)
        fake_devices = [_FakeDevice(), _FakeDevice(), _FakeDevice(), _FakeDevice()]
        svc = ControllerService(
            _make_mirrored_lr_config(),
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fake_devices),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )

        reply = svc.handle_cmd({'id': 25, 'cmd': 'load_program', 'program_id': 'duo_show'})

        assert reply['ok'] is True
        assert [dev.load_calls for dev in fake_devices] == [1, 1, 1, 1]

        snap = svc.build_snapshot()
        assert snap['session']['strips'] == [
            {
                'name': 'left',
                'length': 5,
                'targets': [
                    {'device_id': 1, 'device_uid': 'sim-left', 'device_type': 'sim', 'length': 5, 'session_role': 'serving'},
                    {'device_id': 2, 'device_uid': 'esp-left', 'device_type': 'esp32', 'length': 5, 'session_role': 'serving'},
                ],
            },
            {
                'name': 'right',
                'length': 5,
                'targets': [
                    {'device_id': 3, 'device_uid': 'sim-right', 'device_type': 'sim', 'length': 5, 'session_role': 'serving'},
                    {'device_id': 4, 'device_uid': 'esp-right', 'device_type': 'esp32', 'length': 5, 'session_role': 'serving'},
                ],
            },
        ]

    def test_session_strip_metadata_keeps_logical_length_when_target_is_longer(self):
        config = Config(frame_port=1, devices=[
            DeviceConfig(1, 'esp-main', 'esp32', '127.0.0.1', 9001, 'main', 10),
        ])
        entry = _make_program_entry(
            'short_show',
            source="""\
from elements.dsl import strip, spark, sec
s = strip('main', length=5)
sp = spark(color='white', fade=1.0)
sp.schedule(s.pixels('0-4'), at=0, duration=sec(0.5))
""",
        )
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory([_FakeDevice()]),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )

        reply = svc.handle_cmd({'id': 26, 'cmd': 'load_program', 'program_id': 'short_show'})

        assert reply['ok'] is True
        assert svc.build_snapshot()['session']['strips'] == [
            {
                'name': 'main',
                'length': 5,
                'targets': [
                    {'device_id': 1, 'device_uid': 'esp-main', 'device_type': 'esp32', 'length': 10, 'session_role': 'serving'},
                ],
            }
        ]


class TestScenePlanning:
    def test_prepare_scene_plan_valid(self):
        entries = [
            _make_program_entry('main_show', source=_MAIN_144_DSL),
            _make_program_entry('duo_show', source=_LEFT_RIGHT_DSL),
        ]
        fake_devices = [_FakeDevice(), _FakeDevice(), _FakeDevice(), _FakeDevice()]
        svc = ControllerService(
            _make_mirrored_lr_config(),
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fake_devices),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, entries),
        )

        plan = svc._prepare_scene_plan({
            'loop': True,
            'entries': [
                {'program_id': 'duo_show', 'targets': ['sim-left', 'esp-left', 'sim-right', 'esp-right']},
            ],
        })

        assert plan['loop'] is True
        assert [entry['program_id'] for entry in plan['entries']] == ['duo_show']
        assert plan['entries'][0]['targets'] == ['sim-left', 'esp-left', 'sim-right', 'esp-right']
        assert plan['entries'][0]['target_groups'] == [[0, 1], [2, 3]]
        assert plan['entries'][0]['duration'] == 0.5
        assert isinstance(plan['entries'][0]['safe_intervals'], list)

    def test_prepare_scene_plan_preserves_request_order(self):
        entries = [
            _make_program_entry('main_show', source=_MAIN_144_DSL),
            _make_program_entry('duo_show', source=_LEFT_RIGHT_DSL),
        ]
        fake_devices = [_FakeDevice(), _FakeDevice(), _FakeDevice(), _FakeDevice()]
        svc = ControllerService(
            Config(frame_port=1, devices=[
                DeviceConfig(1, 'sim-main', 'sim', '127.0.0.1', 9001, 'main', 144),
                DeviceConfig(2, 'sim-left', 'sim', '127.0.0.1', 9002, 'left', 5),
                DeviceConfig(3, 'sim-right', 'sim', '127.0.0.1', 9003, 'right', 5),
                DeviceConfig(4, 'esp-right', 'esp32', '127.0.0.1', 9004, 'right', 5),
            ]),
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fake_devices),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, entries),
        )

        plan = svc._prepare_scene_plan({
            'entries': [
                {'program_id': 'duo_show', 'targets': ['sim-left', 'sim-right', 'esp-right']},
                {'program_id': 'main_show', 'targets': ['sim-main']},
            ],
        })

        assert [entry['program_id'] for entry in plan['entries']] == ['duo_show', 'main_show']

    def test_prepare_scene_plan_empty_entries_rejected(self):
        svc, _ = _make_service()

        with pytest.raises(ValueError, match="'entries' must be a non-empty list"):
            svc._prepare_scene_plan({'entries': []})

    def test_prepare_scene_plan_missing_program_id_rejected(self):
        svc, _ = _make_service()

        with pytest.raises(ValueError, match="entry 1: missing 'program_id' field"):
            svc._prepare_scene_plan({'entries': [{'targets': ['sim-1']}]})

    def test_prepare_scene_plan_missing_targets_rejected(self):
        entry = _make_program_entry('main_show')
        svc, _ = _make_service(
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )

        with pytest.raises(ValueError, match=r"entry 1 \(main_show\): missing 'targets' field"):
            svc._prepare_scene_plan({'entries': [{'program_id': 'main_show'}]})

    def test_prepare_scene_plan_empty_targets_rejected(self):
        entry = _make_program_entry('main_show')
        svc, _ = _make_service(
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )

        with pytest.raises(ValueError, match=r"entry 1 \(main_show\): 'targets' must be a non-empty list"):
            svc._prepare_scene_plan({'entries': [{'program_id': 'main_show', 'targets': []}]})

    def test_prepare_scene_plan_unknown_program_rejected_with_context(self):
        svc, _ = _make_service()

        with pytest.raises(ValueError, match=r"entry 1 \(missing\): program not found: missing"):
            svc._prepare_scene_plan({'entries': [{'program_id': 'missing', 'targets': ['sim-1']}]})

    def test_prepare_scene_plan_broken_program_rejected_with_context(self):
        entry = _make_program_entry('broken', error='missing DURATION')
        svc, _ = _make_service(
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )

        with pytest.raises(
            ValueError,
            match=r"entry 1 \(broken\): program broken is not loadable: missing DURATION",
        ):
            svc._prepare_scene_plan({'entries': [{'program_id': 'broken', 'targets': ['sim-1']}]})

    def test_prepare_scene_plan_unknown_target_rejected_with_context(self):
        entry = _make_program_entry('main_show')
        svc, _ = _make_service(
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )

        with pytest.raises(
            ValueError,
            match=r"entry 1 \(main_show\): target not found: missing",
        ):
            svc._prepare_scene_plan({'entries': [{'program_id': 'main_show', 'targets': ['missing']}]})

    def test_prepare_scene_plan_duplicate_target_within_entry_rejected_with_context(self):
        entry = _make_program_entry('main_show')
        svc, _ = _make_service(
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )

        with pytest.raises(
            ValueError,
            match=r"entry 1 \(main_show\): duplicate target: sim-1",
        ):
            svc._prepare_scene_plan({
                'entries': [{'program_id': 'main_show', 'targets': ['sim-1', 'sim-1']}],
            })

    def test_prepare_scene_plan_duplicate_target_across_entries_rejected(self):
        entry = _make_program_entry('main_show', source=_MAIN_144_DSL)
        fake_devices = [_FakeDevice(), _FakeDevice()]
        svc = ControllerService(
            _make_mirrored_main_config(),
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fake_devices),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )

        with pytest.raises(
            ValueError,
            match=r"entry 2 \(main_show\): duplicate target across scene: sim-144 already used by entry 1 \(main_show\)",
        ):
            svc._prepare_scene_plan({
                'entries': [
                    {'program_id': 'main_show', 'targets': ['sim-144']},
                    {'program_id': 'main_show', 'targets': ['sim-144']},
                ],
            })

    def test_prepare_scene_plan_missing_strip_coverage_rejected_with_context(self):
        entry = _make_program_entry('duo_show', source=_LEFT_RIGHT_DSL)
        fake_devices = [_FakeDevice(), _FakeDevice(), _FakeDevice(), _FakeDevice()]
        svc = ControllerService(
            _make_mirrored_lr_config(),
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fake_devices),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )

        with pytest.raises(
            ValueError,
            match=r"entry 1 \(duo_show\): missing targets for strip: right",
        ):
            svc._prepare_scene_plan({
                'entries': [{'program_id': 'duo_show', 'targets': ['sim-left', 'esp-left']}],
            })

    def test_prepare_scene_plan_unused_target_rejected_with_context(self):
        entry = _make_program_entry('main_show', source=_MAIN_144_DSL)
        config = Config(frame_port=1, devices=[
            DeviceConfig(1, 'sim-144', 'sim', '127.0.0.1', 9001, 'main', 144),
            DeviceConfig(2, 'sim-bench', 'sim', '127.0.0.1', 9002, 'bench', 144),
        ])
        fake_devices = [_FakeDevice(), _FakeDevice()]
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fake_devices),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )

        with pytest.raises(
            ValueError,
            match=r"entry 1 \(main_show\): unused target: sim-bench",
        ):
            svc._prepare_scene_plan({
                'entries': [{'program_id': 'main_show', 'targets': ['sim-144', 'sim-bench']}],
            })

    def test_prepare_scene_plan_shorter_target_rejected_with_context(self):
        entry = _make_program_entry('main_show', source=_MAIN_144_DSL)
        config = Config(frame_port=1, devices=[
            DeviceConfig(1, 'sim-100', 'sim', '127.0.0.1', 9001, 'main', 100),
        ])
        fake_devices = [_FakeDevice()]
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fake_devices),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )

        with pytest.raises(
            ValueError,
            match=r"entry 1 \(main_show\): strip 'main' length 144 exceeds configured length 100",
        ):
            svc._prepare_scene_plan({
                'entries': [{'program_id': 'main_show', 'targets': ['sim-100']}],
            })

    def test_prepare_scene_plan_same_program_disjoint_targets_reuses_cache(self, monkeypatch):
        entry = _make_program_entry('main_show', source=_MAIN_144_DSL)
        fake_devices = [_FakeDevice(), _FakeDevice()]
        svc = ControllerService(
            _make_mirrored_main_config(),
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fake_devices),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )
        calls = []
        original = svc._compile

        def wrapped_compile(source, beat, duration):
            calls.append((source, beat, duration))
            return original(source, beat, duration)

        monkeypatch.setattr(svc, '_compile', wrapped_compile)

        plan = svc._prepare_scene_plan({
            'entries': [
                {'program_id': 'main_show', 'targets': ['sim-144']},
                {'program_id': 'main_show', 'targets': ['esp-144']},
            ],
        })

        assert [entry['targets'] for entry in plan['entries']] == [['sim-144'], ['esp-144']]
        assert len(calls) == 1


class TestSceneLoading:
    def test_intersect_safe_intervals_overlap(self):
        svc, _ = _make_service()
        assert svc._intersect_safe_intervals(
            [(0.0, 0.0), (1.0, 4.0)],
            [(0.0, 0.0), (2.0, 5.0)],
        ) == [(0.0, 0.0), (2.0, 4.0)]

    def test_intersect_safe_intervals_disjoint(self):
        svc, _ = _make_service()
        assert svc._intersect_safe_intervals(
            [(0.0, 0.0), (1.0, 2.0)],
            [(3.0, 4.0)],
        ) == []

    def test_intersect_safe_intervals_empty_side(self):
        svc, _ = _make_service()
        assert svc._intersect_safe_intervals([(0.0, 1.0)], []) == []

    def test_load_scene_valid(self):
        entries = [
            _make_program_entry('main_show', source=_MAIN_144_DSL),
            _make_program_entry('duo_show', source=_LEFT_RIGHT_DSL),
        ]
        config = Config(frame_port=1, devices=[
            DeviceConfig(1, 'sim-144', 'sim', '127.0.0.1', 9001, 'main', 144),
            DeviceConfig(2, 'esp-144', 'esp32', '127.0.0.1', 9002, 'main', 144),
            DeviceConfig(3, 'sim-left', 'sim', '127.0.0.1', 9003, 'left', 5),
            DeviceConfig(4, 'esp-left', 'esp32', '127.0.0.1', 9004, 'left', 5),
            DeviceConfig(5, 'sim-right', 'sim', '127.0.0.1', 9005, 'right', 5),
            DeviceConfig(6, 'esp-right', 'esp32', '127.0.0.1', 9006, 'right', 5),
        ])
        fake_devices = [_FakeDevice() for _ in range(6)]
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fake_devices),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, entries),
        )

        reply = svc.handle_cmd({
            'id': 40,
            'cmd': 'load_scene',
            'loop': True,
            'entries': [
                {'program_id': 'main_show', 'targets': ['sim-144', 'esp-144']},
                {'program_id': 'duo_show', 'targets': ['sim-left', 'esp-left', 'sim-right', 'esp-right']},
            ],
        })

        assert reply['ok'] is True
        assert reply['result']['session_id'] == 1
        assert [dev.load_calls for dev in fake_devices] == [1, 1, 1, 1, 1, 1]

        snap = svc.build_snapshot()
        assert snap['session']['duration'] == 0.5
        assert snap['session']['strips'] == [
            {
                'name': 'main',
                'length': 144,
                'targets': [
                    {'device_id': 1, 'device_uid': 'sim-144', 'device_type': 'sim', 'length': 144, 'session_role': 'serving'},
                    {'device_id': 2, 'device_uid': 'esp-144', 'device_type': 'esp32', 'length': 144, 'session_role': 'serving'},
                ],
            },
            {
                'name': 'left',
                'length': 5,
                'targets': [
                    {'device_id': 3, 'device_uid': 'sim-left', 'device_type': 'sim', 'length': 5, 'session_role': 'serving'},
                    {'device_id': 4, 'device_uid': 'esp-left', 'device_type': 'esp32', 'length': 5, 'session_role': 'serving'},
                ],
            },
            {
                'name': 'right',
                'length': 5,
                'targets': [
                    {'device_id': 5, 'device_uid': 'sim-right', 'device_type': 'sim', 'length': 5, 'session_role': 'serving'},
                    {'device_id': 6, 'device_uid': 'esp-right', 'device_type': 'esp32', 'length': 5, 'session_role': 'serving'},
                ],
            },
        ]

        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)
        session_start = next(e for e in events if e.get('event') == 'session_start')
        assert session_start['strips'] == snap['session']['strips']

    def test_load_scene_repeated_program_id_disjoint_targets_allowed(self):
        entry = _make_program_entry('main_show', source=_MAIN_144_DSL)
        fake_devices = [_FakeDevice(), _FakeDevice()]
        svc = ControllerService(
            _make_mirrored_main_config(),
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fake_devices),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )

        reply = svc.handle_cmd({
            'id': 41,
            'cmd': 'load_scene',
            'entries': [
                {'program_id': 'main_show', 'targets': ['sim-144']},
                {'program_id': 'main_show', 'targets': ['esp-144']},
            ],
        })

        assert reply['ok'] is True
        assert [dev.load_calls for dev in fake_devices] == [1, 1]
        assert svc.build_snapshot()['session']['strips'] == [
            {
                'name': 'main',
                'length': 144,
                'targets': [{'device_id': 1, 'device_uid': 'sim-144', 'device_type': 'sim', 'length': 144, 'session_role': 'serving'}],
            },
            {
                'name': 'main',
                'length': 144,
                'targets': [{'device_id': 2, 'device_uid': 'esp-144', 'device_type': 'esp32', 'length': 144, 'session_role': 'serving'}],
            },
        ]

    def test_load_scene_rejects_mismatched_durations(self):
        entries = [
            _make_program_entry('short_show', source=_SIMPLE_DSL, duration=0.5),
            _make_program_entry(
                'long_show',
                source="BEAT = 1.0\nDURATION = 1.0\n" + _SIMPLE_DSL,
                duration=1.0,
            ),
        ]
        config = Config(frame_port=1, devices=[
            DeviceConfig(1, 'sim-1', 'sim', '127.0.0.1', 9001, 'test', 5),
            DeviceConfig(2, 'sim-2', 'sim', '127.0.0.1', 9002, 'test', 5),
        ])
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory([_FakeDevice(), _FakeDevice()]),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, entries),
        )

        reply = svc.handle_cmd({
            'id': 42,
            'cmd': 'load_scene',
            'entries': [
                {'program_id': 'short_show', 'targets': ['sim-1']},
                {'program_id': 'long_show', 'targets': ['sim-2']},
            ],
        })

        assert reply['ok'] is False
        assert 'scene entries must share one duration' in reply['error']

    def test_load_scene_rejects_duplicate_target_across_entries(self):
        entry = _make_program_entry('main_show', source=_MAIN_144_DSL)
        fake_devices = [_FakeDevice(), _FakeDevice()]
        svc = ControllerService(
            _make_mirrored_main_config(),
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fake_devices),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )

        reply = svc.handle_cmd({
            'id': 43,
            'cmd': 'load_scene',
            'entries': [
                {'program_id': 'main_show', 'targets': ['sim-144']},
                {'program_id': 'main_show', 'targets': ['sim-144']},
            ],
        })

        assert reply['ok'] is False
        assert (
            reply['error']
            == 'entry 2 (main_show): duplicate target across scene: sim-144 already used by entry 1 (main_show)'
        )

    def test_load_scene_allows_empty_merged_safe_intervals(self):
        config = Config(frame_port=1, devices=[
            DeviceConfig(1, 'sim-1', 'sim', '127.0.0.1', 9001, 'test-a', 5),
            DeviceConfig(2, 'sim-2', 'sim', '127.0.0.1', 9002, 'test-b', 5),
        ])
        fake_devices = [_FakeDevice(), _FakeDevice()]
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fake_devices),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir),
        )

        def fake_prepare(cmd):
            return {
                'loop': cmd.get('loop', False),
                'entries': [
                    {
                        'program_id': 'alpha',
                        'targets': ['sim-1'],
                        'manifest': CompiledManifest(
                            duration=1.0,
                            strips=[CompiledStripArtifact('test-a', 5, b'a')],
                            safe_intervals=[(0.0, 0.0), (1.0, 2.0)],
                        ),
                        'target_groups': [[0]],
                        'duration': 1.0,
                        'safe_intervals': [(0.0, 0.0), (1.0, 2.0)],
                    },
                    {
                        'program_id': 'beta',
                        'targets': ['sim-2'],
                        'manifest': CompiledManifest(
                            duration=1.0,
                            strips=[CompiledStripArtifact('test-b', 5, b'b')],
                            safe_intervals=[(3.0, 4.0)],
                        ),
                        'target_groups': [[1]],
                        'duration': 1.0,
                        'safe_intervals': [(3.0, 4.0)],
                    },
                ],
            }

        svc._prepare_scene_plan = fake_prepare

        reply = svc.handle_cmd({
            'id': 44,
            'cmd': 'load_scene',
            'entries': [{'program_id': 'ignored', 'targets': ['sim-1']}],
        })

        assert reply['ok'] is True
        assert svc.build_snapshot()['session']['safe_intervals'] == []

    def test_publish_program_same_source_keeps_cache_hit(self, monkeypatch):
        fake_library = _FakeLibrary('/tmp/programs')
        svc, _ = _make_service(
            library_factory=lambda animations_dir: fake_library,
        )
        source = "BEAT = 1.0\nDURATION = 0.5\n" + _SIMPLE_DSL
        calls = []
        original = svc._compile

        def wrapped_compile(source, beat, duration):
            calls.append((source, beat, duration))
            return original(source, beat, duration)

        monkeypatch.setattr(svc, '_compile', wrapped_compile)

        assert svc.handle_cmd({
            'id': 1,
            'cmd': 'publish_program',
            'program_id': 'main_show',
            'source': source,
        })['ok'] is True
        assert svc.handle_cmd({'id': 2, 'cmd': 'load_program', 'program_id': 'main_show'})['ok'] is True
        assert svc.handle_cmd({
            'id': 3,
            'cmd': 'publish_program',
            'program_id': 'main_show',
            'source': source,
        })['ok'] is True
        assert svc.handle_cmd({'id': 4, 'cmd': 'load_program', 'program_id': 'main_show'})['ok'] is True

        assert len(calls) == 1

    def test_publish_program_changed_source_recompiles(self, monkeypatch):
        fake_library = _FakeLibrary('/tmp/programs')
        svc, _ = _make_service(
            library_factory=lambda animations_dir: fake_library,
        )
        source1 = "BEAT = 1.0\nDURATION = 0.5\n" + _SIMPLE_DSL
        source2 = "BEAT = 1.0\nDURATION = 1.0\n" + _SIMPLE_DSL
        calls = []
        original = svc._compile

        def wrapped_compile(source, beat, duration):
            calls.append((source, beat, duration))
            return original(source, beat, duration)

        monkeypatch.setattr(svc, '_compile', wrapped_compile)

        assert svc.handle_cmd({
            'id': 1,
            'cmd': 'publish_program',
            'program_id': 'main_show',
            'source': source1,
        })['ok'] is True
        assert svc.handle_cmd({'id': 2, 'cmd': 'load_program', 'program_id': 'main_show'})['ok'] is True
        assert svc.handle_cmd({
            'id': 3,
            'cmd': 'publish_program',
            'program_id': 'main_show',
            'source': source2,
        })['ok'] is True
        assert svc.handle_cmd({'id': 4, 'cmd': 'load_program', 'program_id': 'main_show'})['ok'] is True

        assert len(calls) == 2

    def test_load_program_uses_cache_on_second_load(self, monkeypatch):
        entry = _make_program_entry('main_show')
        svc, _ = _make_service(
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir, [entry]),
        )
        calls = []
        original = svc._compile

        def wrapped_compile(source, beat, duration):
            calls.append((source, beat, duration))
            return original(source, beat, duration)

        monkeypatch.setattr(svc, '_compile', wrapped_compile)

        reply1 = svc.handle_cmd({'id': 1, 'cmd': 'load_program', 'program_id': 'main_show'})
        reply2 = svc.handle_cmd({'id': 2, 'cmd': 'load_program', 'program_id': 'main_show'})

        assert reply1['ok'] is True
        assert reply2['ok'] is True
        assert len(calls) == 1

    def test_load_program_recompiles_after_topology_change(self, monkeypatch, tmp_path):
        entry = _make_program_entry('main_show')
        config = _make_config()
        config.animations_dir = str(tmp_path)
        fake_library = _FakeLibrary(str(tmp_path), [entry])
        svc, _path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=lambda *args, **kwargs: _FakeDevice(),
            library_factory=lambda animations_dir: fake_library,
        )
        calls = []
        original = svc._compile

        def wrapped_compile(source, beat, duration):
            calls.append((source, beat, duration))
            return original(source, beat, duration)

        monkeypatch.setattr(svc, '_compile', wrapped_compile)

        reply1 = svc.handle_cmd({'id': 1, 'cmd': 'load_program', 'program_id': 'main_show'})
        assert reply1['ok'] is True

        candidate = copy.deepcopy(svc._raw_doc)
        candidate['devices'][0]['length'] = 8
        new_config = load_config_obj(candidate)
        svc._raw_doc = candidate
        svc._reconcile_devices(new_config)
        svc._rebuild_controller()

        reply2 = svc.handle_cmd({'id': 2, 'cmd': 'load_program', 'program_id': 'main_show'})

        assert reply2['ok'] is True
        assert len(calls) == 2


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


class TestRebootDevice:
    def test_reboot_device_calls_connected_esp32_runtime(self):
        fake = _FakeDevice()
        config = Config(
            frame_port=1,
            devices=[
                DeviceConfig(
                    device_id=1,
                    device_uid='esp32-246f28b5f190',
                    device_type='esp32',
                    host='127.0.0.1',
                    tcp_port=9001,
                    strip_id='test',
                    length=5,
                ),
            ],
        )
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory([fake]),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir),
        )

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'reboot_device',
            'device_uid': 'esp32-246f28b5f190',
        })

        assert reply['ok'] is True
        assert reply['result']['message'] == 'reboot requested for esp32-246f28b5f190'
        assert fake.reboot_calls == 1
        assert fake.is_connected is False
        assert svc._disconnect_reasons[1] == 'reboot requested'

    def test_reboot_device_rejects_unknown_uid(self):
        svc, _ = _make_service()

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'reboot_device',
            'device_uid': 'missing',
        })

        assert reply['ok'] is False
        assert reply['error'] == 'device not found: missing'

    def test_reboot_device_rejects_non_esp32(self):
        svc, fakes = _make_service()

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'reboot_device',
            'device_uid': 'sim-1',
        })

        assert reply['ok'] is False
        assert reply['error'] == 'device reboot is only supported for esp32 devices'
        assert fakes[0].reboot_calls == 0

    def test_reboot_device_requires_connected_device(self):
        fake = _FakeDevice()
        fake.is_connected = False
        config = Config(
            frame_port=1,
            devices=[
                DeviceConfig(
                    device_id=1,
                    device_uid='esp32-246f28b5f190',
                    device_type='esp32',
                    host='127.0.0.1',
                    tcp_port=9001,
                    strip_id='test',
                    length=5,
                ),
            ],
        )
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory([fake]),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir),
        )

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'reboot_device',
            'device_uid': 'esp32-246f28b5f190',
        })

        assert reply['ok'] is False
        assert reply['error'] == 'device is not currently connected'
        assert fake.reboot_calls == 0


class TestConfigMutations:
    def test_add_device_accepts_provisional_esp32_short_uid(self, tmp_path):
        config = _make_config(n_devices=1)
        config.discovery_port = 6040
        svc, path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=lambda *args, **kwargs: _FakeDevice(),
        )

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'add_device',
            'device_type': 'esp32',
            'device_uid': 'B5F190',
            'strip_id': 'strip_c',
            'length': 7,
        })

        assert reply['ok'] is True
        assert reply['result']['message'] == 'added device b5f190'
        saved = load_config(str(path))
        assert [dc.device_uid for dc in saved.devices] == ['sim-1', 'b5f190']

    def test_add_device_canonicalizes_esp32_full_hex_uid(self, tmp_path):
        config = _make_config(n_devices=1)
        config.discovery_port = 6040
        svc, path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=lambda *args, **kwargs: _FakeDevice(),
        )

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'add_device',
            'device_type': 'esp32',
            'device_uid': '246F28B5F190',
            'strip_id': 'strip_c',
            'length': 7,
        })

        assert reply['ok'] is True
        assert reply['result']['message'] == 'added device esp32-246f28b5f190'
        saved = load_config(str(path))
        assert [dc.device_uid for dc in saved.devices] == ['sim-1', 'esp32-246f28b5f190']

    def test_add_device_rejects_esp32_short_uid_conflicting_with_full_uid(self, tmp_path):
        config = Config(
            frame_port=1,
            discovery_port=6040,
            devices=[
                DeviceConfig(
                    device_id=1,
                    device_uid='esp32-246f28b5f190',
                    device_type='esp32',
                    host='',
                    tcp_port=0,
                    strip_id='main',
                    length=60,
                ),
            ],
        )
        svc, _path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=lambda *args, **kwargs: _FakeDevice(),
        )

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'add_device',
            'device_type': 'esp32',
            'device_uid': 'b5f190',
            'strip_id': 'aux',
            'length': 30,
        })

        assert reply['ok'] is False
        assert reply['error'] == 'device uid already exists: b5f190'

    def test_add_device_rejects_esp32_full_hex_conflicting_with_canonical_uid(self, tmp_path):
        config = Config(
            frame_port=1,
            discovery_port=6040,
            devices=[
                DeviceConfig(
                    device_id=1,
                    device_uid='esp32-246f28b5f190',
                    device_type='esp32',
                    host='',
                    tcp_port=0,
                    strip_id='main',
                    length=60,
                ),
            ],
        )
        svc, _path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=lambda *args, **kwargs: _FakeDevice(),
        )

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'add_device',
            'device_type': 'esp32',
            'device_uid': '246f28b5f190',
            'strip_id': 'aux',
            'length': 30,
        })

        assert reply['ok'] is False
        assert reply['error'] == 'device uid already exists: esp32-246f28b5f190'

    def test_add_device_reuses_unchanged_devices_and_saves(self, tmp_path):
        config = _make_config(n_devices=2)
        config.discovery_port = 6040
        created: list[_AddressableFakeDevice] = []

        def factory(device_id, host, tcp_port, device_type, strip_length, frame_port, udp_receiver):
            dev = _AddressableFakeDevice()
            created.append(dev)
            return dev

        svc, path = _make_service_with_path(tmp_path, config, device_factory=factory)
        initial_devices = list(svc._devices)

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'add_device',
            'device_type': 'sim',
            'device_uid': 'sim-3',
            'strip_id': 'strip_c',
            'length': 7,
        })

        assert reply['ok'] is True
        assert reply['result']['message'] == 'added device sim-3'
        assert len(created) == 3
        assert svc._devices[0] is initial_devices[0]
        assert svc._devices[1] is initial_devices[1]
        assert svc._device_configs[2].device_uid == 'sim-3'

        saved = load_config(str(path))
        assert [dc.device_uid for dc in saved.devices] == ['sim-1', 'sim-2', 'sim-3']

        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)
        snapshots = [e for e in events if e.get('event') == 'snapshot']
        assert snapshots
        assert [d['device_uid'] for d in snapshots[0]['devices']] == ['sim-1', 'sim-2', 'sim-3']

    def test_remove_device_reuses_unchanged_devices_and_saves(self, tmp_path):
        config = _make_config(n_devices=2)
        created: list[_AddressableFakeDevice] = []

        def factory(device_id, host, tcp_port, device_type, strip_length, frame_port, udp_receiver):
            dev = _AddressableFakeDevice()
            created.append(dev)
            return dev

        svc, path = _make_service_with_path(tmp_path, config, device_factory=factory)
        initial_devices = list(svc._devices)
        removed = initial_devices[0]

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'remove_device',
            'device_uid': 'sim-1',
        })

        assert reply['ok'] is True
        assert reply['result']['message'] == 'removed device sim-1'
        assert len(svc._devices) == 1
        assert svc._devices[0] is initial_devices[1]
        assert removed.close_calls == 1

        saved = load_config(str(path))
        assert [dc.device_uid for dc in saved.devices] == ['sim-2']

    def test_edit_device_reuses_unchanged_devices_and_saves(self, tmp_path):
        config = _make_config(n_devices=2)
        created: list[_AddressableFakeDevice] = []

        def factory(device_id, host, tcp_port, device_type, strip_length, frame_port, udp_receiver):
            dev = _AddressableFakeDevice()
            created.append(dev)
            return dev

        svc, path = _make_service_with_path(tmp_path, config, device_factory=factory)
        initial_devices = list(svc._devices)
        edited = initial_devices[0]

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'edit_device',
            'target_device_uid': 'sim-1',
            'device_uid': 'sim-1',
            'strip_id': 'strip_a',
            'length': 8,
        })

        assert reply['ok'] is True
        assert reply['result']['message'] == 'updated device sim-1'
        assert len(created) == 3
        assert svc._devices[0] is not edited
        assert svc._devices[1] is initial_devices[1]
        assert edited.close_calls == 1
        assert svc._device_configs[0].length == 8

        saved = load_config(str(path))
        assert [(dc.device_uid, dc.strip_id, dc.length) for dc in saved.devices] == [
            ('sim-1', 'strip_a', 8),
            ('sim-2', 'strip_b', 5),
        ]

        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)
        snapshots = [e for e in events if e.get('event') == 'snapshot']
        assert snapshots
        assert snapshots[0]['devices'][0]['length'] == 8

    def test_edit_device_can_rename_uid(self, tmp_path):
        config = _make_config(n_devices=2)
        svc, path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=lambda *args, **kwargs: _FakeDevice(),
        )

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'edit_device',
            'target_device_uid': 'sim-1',
            'device_uid': 'sim-1-fixed',
            'strip_id': 'strip_a',
            'length': 5,
        })

        assert reply['ok'] is True
        assert reply['result']['message'] == 'updated device sim-1 -> sim-1-fixed'
        saved = load_config(str(path))
        assert [dc.device_uid for dc in saved.devices] == ['sim-1-fixed', 'sim-2']

    def test_edit_device_can_change_strip_id(self, tmp_path):
        config = _make_config(n_devices=2)
        svc, path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=lambda *args, **kwargs: _FakeDevice(),
        )

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'edit_device',
            'target_device_uid': 'sim-1',
            'device_uid': 'sim-1',
            'strip_id': 'main_left',
            'length': 5,
        })

        assert reply['ok'] is True
        saved = load_config(str(path))
        assert [dc.strip_id for dc in saved.devices] == ['main_left', 'strip_b']

    def test_edit_device_missing_target_rejected(self, tmp_path):
        config = _make_config(n_devices=2)
        svc, _path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=lambda *args, **kwargs: _FakeDevice(),
        )

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'edit_device',
            'target_device_uid': 'missing',
            'device_uid': 'missing',
            'strip_id': 'strip_x',
            'length': 5,
        })

        assert reply['ok'] is False
        assert reply['error'] == 'device not found: missing'

    def test_edit_device_duplicate_uid_rejected(self, tmp_path):
        config = _make_config(n_devices=2)
        svc, _path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=lambda *args, **kwargs: _FakeDevice(),
        )

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'edit_device',
            'target_device_uid': 'sim-1',
            'device_uid': 'sim-2',
            'strip_id': 'strip_a',
            'length': 5,
        })

        assert reply['ok'] is False
        assert reply['error'] == 'device uid already exists: sim-2'

    def test_edit_device_duplicate_strip_id_rejected(self, tmp_path):
        config = _make_config(n_devices=2)
        svc, _path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=lambda *args, **kwargs: _FakeDevice(),
        )

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'edit_device',
            'target_device_uid': 'sim-1',
            'device_uid': 'sim-1',
            'strip_id': 'strip_b',
            'length': 5,
        })

        assert reply['ok'] is True

    def test_add_device_duplicate_strip_id_same_length_allowed(self, tmp_path):
        config = _make_config(n_devices=1)
        config.discovery_port = 6040
        svc, path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=lambda *args, **kwargs: _FakeDevice(),
        )

        reply = svc.handle_cmd({
            'id': 20,
            'cmd': 'add_device',
            'device_type': 'sim',
            'device_uid': 'sim-2',
            'strip_id': 'test',
            'length': 5,
        })

        assert reply['ok'] is True
        saved = load_config(str(path))
        assert [(dc.device_uid, dc.strip_id, dc.length) for dc in saved.devices] == [
            ('sim-1', 'test', 5),
            ('sim-2', 'test', 5),
        ]

    def test_add_device_duplicate_strip_id_different_length_rejected(self, tmp_path):
        config = _make_config(n_devices=1)
        config.discovery_port = 6040
        svc, _path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=lambda *args, **kwargs: _FakeDevice(),
        )

        reply = svc.handle_cmd({
            'id': 21,
            'cmd': 'add_device',
            'device_type': 'sim',
            'device_uid': 'sim-2',
            'strip_id': 'test',
            'length': 7,
        })

        assert reply['ok'] is False
        assert reply['error'] == "duplicate strip_id with different length: 'test'"

    def test_add_device_length_above_max_rejected(self, tmp_path):
        config = _make_config(n_devices=1)
        config.discovery_port = 6040
        svc, _path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=lambda *args, **kwargs: _FakeDevice(),
        )

        reply = svc.handle_cmd({
            'id': 21,
            'cmd': 'add_device',
            'device_type': 'sim',
            'device_uid': 'sim-too-long',
            'strip_id': 'strip_c',
            'length': MAX_DEVICE_PIXELS + 1,
        })

        assert reply['ok'] is False
        assert reply['error'] == f'length must be 1-{MAX_DEVICE_PIXELS}, got {MAX_DEVICE_PIXELS + 1}'

    def test_edit_device_duplicate_strip_id_same_length_allowed(self, tmp_path):
        config = _make_config(n_devices=2)
        svc, path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=lambda *args, **kwargs: _FakeDevice(),
        )

        reply = svc.handle_cmd({
            'id': 22,
            'cmd': 'edit_device',
            'target_device_uid': 'sim-1',
            'device_uid': 'sim-1',
            'strip_id': 'strip_b',
            'length': 5,
        })

        assert reply['ok'] is True
        saved = load_config(str(path))
        assert [(dc.device_uid, dc.strip_id, dc.length) for dc in saved.devices] == [
            ('sim-1', 'strip_b', 5),
            ('sim-2', 'strip_b', 5),
        ]

    def test_edit_device_duplicate_strip_id_different_length_rejected(self, tmp_path):
        config = _make_config(n_devices=2)
        svc, _path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=lambda *args, **kwargs: _FakeDevice(),
        )

        reply = svc.handle_cmd({
            'id': 23,
            'cmd': 'edit_device',
            'target_device_uid': 'sim-1',
            'device_uid': 'sim-1',
            'strip_id': 'strip_b',
            'length': 7,
        })

        assert reply['ok'] is False
        assert reply['error'] == "duplicate strip_id with different length: 'strip_b'"

    def test_mutations_reject_when_controller_loaded(self, tmp_path):
        config = _make_config()
        svc, _path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=lambda *args, **kwargs: _FakeDevice(),
        )

        svc.handle_cmd({
            'id': 1, 'cmd': 'load',
            'source': _SIMPLE_DSL, 'beat': 1.0, 'duration': 0.5,
        })

        reply = svc.handle_cmd({
            'id': 2,
            'cmd': 'add_device',
            'device_type': 'sim',
            'device_uid': 'sim-2',
            'strip_id': 'strip_b',
            'length': 5,
        })

        assert reply['ok'] is False
        assert 'controller must be idle' in reply['error']

        reply2 = svc.handle_cmd({
            'id': 3,
            'cmd': 'edit_device',
            'target_device_uid': 'sim-1',
            'device_uid': 'sim-1',
            'strip_id': 'test',
            'length': 8,
        })

        assert reply2['ok'] is False
        assert 'controller must be idle' in reply2['error']

    def test_mutation_quiescence_is_idle_only(self):
        svc, _ = _make_service()

        assert svc._mutation_is_quiescent() is True

        for state in (
            ControllerState.LOADED,
            ControllerState.STOPPED,
            ControllerState.PLAYING,
            ControllerState.PAUSED,
            ControllerState.ENDED,
        ):
            svc._controller._state = state
            assert svc._mutation_is_quiescent() is False

    def test_save_failure_leaves_runtime_state_unchanged(self, monkeypatch, tmp_path):
        config = _make_config()
        config.discovery_port = 6040
        svc, path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=lambda *args, **kwargs: _FakeDevice(),
        )
        initial_devices = list(svc._devices)
        initial_doc = copy.deepcopy(svc._raw_doc)

        monkeypatch.setattr(
            'elemctl.service.save_config_doc',
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError('disk full')),
        )

        reply = svc.handle_cmd({
            'id': 2,
            'cmd': 'add_device',
            'device_type': 'sim',
            'device_uid': 'sim-2',
            'strip_id': 'strip_b',
            'length': 5,
        })

        assert reply['ok'] is False
        assert 'cannot save config' in reply['error']
        assert svc._devices == initial_devices
        assert svc._raw_doc == initial_doc
        saved = load_config(str(path))
        assert [dc.device_uid for dc in saved.devices] == ['sim-1']

    def test_edit_save_failure_leaves_runtime_state_unchanged(self, monkeypatch, tmp_path):
        config = _make_config(n_devices=2)
        svc, path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=lambda *args, **kwargs: _FakeDevice(),
        )
        initial_devices = list(svc._devices)
        initial_doc = copy.deepcopy(svc._raw_doc)

        monkeypatch.setattr(
            'elemctl.service.save_config_doc',
            lambda *args, **kwargs: (_ for _ in ()).throw(OSError('disk full')),
        )

        reply = svc.handle_cmd({
            'id': 2,
            'cmd': 'edit_device',
            'target_device_uid': 'sim-1',
            'device_uid': 'sim-1',
            'strip_id': 'strip_a',
            'length': 9,
        })

        assert reply['ok'] is False
        assert 'cannot save config' in reply['error']
        assert svc._devices == initial_devices
        assert svc._raw_doc == initial_doc
        saved = load_config(str(path))
        assert [(dc.device_uid, dc.strip_id, dc.length) for dc in saved.devices] == [
            ('sim-1', 'strip_a', 5),
            ('sim-2', 'strip_b', 5),
        ]

    def test_add_device_applies_cached_discovery_address(self, tmp_path):
        config = Config(
            frame_port=1,
            discovery_port=9999,
            devices=[
                DeviceConfig(
                    device_id=1,
                    device_uid='sim-1',
                    device_type='sim',
                    host='',
                    tcp_port=0,
                    strip_id='strip_a',
                    length=5,
                )
            ],
        )
        fake_disc = _FakeDiscovery(9999)
        created: list[_AddressableFakeDevice] = []

        def factory(device_id, host, tcp_port, device_type, strip_length, frame_port, udp_receiver):
            dev = _AddressableFakeDevice()
            created.append(dev)
            return dev

        svc, _path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=factory,
            discovery_factory=lambda port: fake_disc,
        )

        fake_disc.inject('sim-2', '127.0.0.1', 9010)
        svc.tick_once()

        reply = svc.handle_cmd({
            'id': 2,
            'cmd': 'add_device',
            'device_type': 'sim',
            'device_uid': 'sim-2',
            'strip_id': 'strip_b',
            'length': 5,
        })

        assert reply['ok'] is True
        added = svc._devices[1]
        assert isinstance(added, _AddressableFakeDevice)
        assert added._host == '127.0.0.1'
        assert added._tcp_port == 9010

    def test_remove_last_device_allowed(self, tmp_path):
        config = _make_config()
        svc, path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=lambda *args, **kwargs: _FakeDevice(),
        )

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'remove_device',
            'device_uid': 'sim-1',
        })

        assert reply['ok'] is True
        saved = load_config(str(path))
        assert saved.devices == []
        assert svc._devices == []
        assert svc.build_snapshot()['expected_count'] == 0

    def test_edit_last_device_allowed(self, tmp_path):
        config = _make_config()
        svc, path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=lambda *args, **kwargs: _FakeDevice(),
        )

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'edit_device',
            'target_device_uid': 'sim-1',
            'device_uid': 'sim-1',
            'strip_id': 'main',
            'length': 42,
        })

        assert reply['ok'] is True
        saved = load_config(str(path))
        assert [(dc.device_uid, dc.strip_id, dc.length) for dc in saved.devices] == [
            ('sim-1', 'main', 42),
        ]

    def test_service_allows_zero_device_config(self):
        svc, _fakes = _make_service(n_devices=0)

        snap = svc.build_snapshot()
        assert svc._devices == []
        assert snap['devices'] == []
        assert snap['expected_count'] == 0
        assert snap['online_count'] == 0

    def test_load_rejected_when_no_devices_configured(self):
        svc, _fakes = _make_service(n_devices=0)

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'load',
            'source': _SIMPLE_DSL,
            'beat': 1.0,
            'duration': 0.5,
        })

        assert reply['ok'] is False
        assert reply['error'] == 'no configured devices'

    def test_load_program_rejected_when_no_devices_configured(self):
        entries = [_make_program_entry('main_show')]
        fake_library = _FakeLibrary('/tmp/programs', entries)
        svc, _fakes = _make_service(
            n_devices=0,
            library_factory=lambda animations_dir: fake_library,
        )

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'load_program',
            'program_id': 'main_show',
        })

        assert reply['ok'] is False
        assert reply['error'] == 'no configured devices'

    def test_publish_program_still_works_with_no_devices(self):
        fake_library = _FakeLibrary('/tmp/programs')
        svc, _fakes = _make_service(
            n_devices=0,
            library_factory=lambda animations_dir: fake_library,
        )

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'publish_program',
            'program_id': 'ambient',
            'source': "BEAT = 1.0\nDURATION = 0.5\n" + _SIMPLE_DSL,
        })

        assert reply['ok'] is True
        assert reply['result']['program']['program_id'] == 'ambient'


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
    """Decode a list of controller-protocol messages into dicts."""
    events = []
    for msg in json_msgs:
        reader = ProtocolReader()
        reader.feed(msg)
        for kind, payload in reader.messages():
            if kind == KIND_JSON:
                events.append(parse_json_payload(payload))
    return events


class TestPresenceEvents:
    def test_offline_to_online_emits_event(self, caplog):
        fakes = [_FakeDevice()]
        fakes[0].is_connected = False
        wall_clock = _FakeWallClock(10.5)
        svc, _ = _make_service(fake_devices=fakes, wall_clock=wall_clock)

        # tick_once probes and reconnects (ensure_connected sets is_connected=True)
        with caplog.at_level(logging.INFO):
            json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)

        status_events = [e for e in events if e.get('event') == 'device_status']
        assert len(status_events) == 2
        evt = next(e for e in status_events if e['source'] == 'connectivity')
        assert evt['source'] == 'connectivity'
        assert evt['connected'] is True
        assert evt['device_id'] == 1
        assert evt['device_uid'] == 'sim-1'
        assert evt['strip'] == 'test'
        assert evt['length'] == 5
        assert evt['last_seen'] == pytest.approx(10.5)
        reported = next(e for e in status_events if e['source'] == 'reported')
        assert reported['reported']['mode'] == 'attached_controlled'

        # Snapshot stays aligned
        snap = svc.build_snapshot()
        assert snap['online_count'] == snap['expected_count']
        assert snap['devices'][0]['last_seen'] == pytest.approx(10.5)
        assert 'device connected: id=1 uid=sim-1 type=sim strip=test host=127.0.0.1 tcp=9000' in caplog.text

    def test_online_to_offline_emits_event(self):
        fakes = [_FakeDevice()]
        fakes[0].is_connected = True
        wall_clock = _FakeWallClock(20.0)
        svc, _ = _make_service(fake_devices=fakes, wall_clock=wall_clock)

        svc.tick_once()
        assert svc.build_snapshot()['devices'][0]['last_seen'] == pytest.approx(20.0)

        # Device goes offline externally; prevent probe from reconnecting
        fakes[0].is_connected = False
        fakes[0].ensure_connected = lambda: False
        wall_clock.now = 27.0
        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)

        status_events = [e for e in events if e.get('event') == 'device_status']
        assert len(status_events) == 1
        assert status_events[0]['source'] == 'connectivity'
        assert status_events[0]['connected'] is False
        assert status_events[0]['last_seen'] == pytest.approx(20.0)

        snap = svc.build_snapshot()
        assert snap['online_count'] == 0
        assert snap['devices'][0]['last_seen'] == pytest.approx(20.0)

    def test_sync_update_emits_device_status_and_sends_result(self):
        fake_device = _FakeDevice()
        fake_sync = _FakeClockSyncManager()
        fake_sync.status_by_device[1] = {
            'clock_state': 'synced',
            'clock_offset_ms': 2.3,
            'clock_rtt_ms': 1.1,
            'clock_last_sync_age_s': 0.0,
        }
        fake_sync.pending_updates.append(types.SimpleNamespace(
            device_id=1,
            clock_state='synced',
            clock_offset_us=2300,
            send_correction=True,
            seq=7,
            boot_token=1234,
            correction_offset_us=2300,
            rtt_us=1100,
        ))
        config = Config(
            frame_port=1,
            devices=[
                DeviceConfig(
                    device_id=1,
                    device_uid='esp32-246f28b5f190',
                    device_type='esp32',
                    host='127.0.0.1',
                    tcp_port=9001,
                    strip_id='test',
                    length=5,
                ),
            ],
        )
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory([fake_device]),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir),
            clock_sync_factory=lambda port, clock_ns: fake_sync,
        )

        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)

        status_events = [e for e in events if e.get('event') == 'device_status']
        assert len(status_events) == 1
        assert status_events[0]['source'] == 'clock'
        assert status_events[0]['clock_state'] == 'synced'
        assert status_events[0]['clock_offset_ms'] == pytest.approx(2.3)
        assert status_events[0]['clock_rtt_ms'] == pytest.approx(1.1)
        assert fake_device.sync_results == [(7, 1234, 2300)]

    def test_sync_activity_prevents_stale_expiry_without_status_update(self):
        fake_device = _FakeDevice()
        fake_sync = _FakeClockSyncManager()
        fake_sync.pending_activity_ids.add(1)
        clock = _FakeClock()
        wall_clock = _FakeWallClock(20.0)
        svc, _, _ = _make_discovery_service(
            n_devices=1,
            fake_devices=[fake_device],
            clock=clock,
            wall_clock=wall_clock,
            clock_sync_factory=lambda port, clock_ns: fake_sync,
        )

        clock.now = 6_500_000_001
        wall_clock.now = 27.0
        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)

        assert [e for e in events if e.get('event') == 'device_status'] == []
        snap = svc.build_snapshot()
        assert snap['devices'][0]['connected'] is True
        assert snap['devices'][0]['last_seen'] == pytest.approx(27.0)
        assert svc._last_activity_source[1] == 'sync'

    def test_last_seen_stays_stable_without_activity(self):
        wall_clock = _FakeWallClock(5.0)
        svc, _ = _make_service(wall_clock=wall_clock)

        svc.tick_once()
        assert svc.build_snapshot()['devices'][0]['last_seen'] == pytest.approx(5.0)

        wall_clock.now = 8.25
        svc.tick_once()
        assert svc.build_snapshot()['devices'][0]['last_seen'] == pytest.approx(5.0)

    def test_discovery_hello_refreshes_last_seen(self):
        fakes = [_FakeDevice()]
        fakes[0].is_connected = True
        clock = _FakeClock()
        wall_clock = _FakeWallClock(5.0)
        svc, _, disc = _make_discovery_service(
            n_devices=1,
            fake_devices=fakes,
            clock=clock,
            wall_clock=wall_clock,
        )

        assert svc.build_snapshot()['devices'][0]['last_seen'] == pytest.approx(5.0)

        clock.now = 1_000_000_000
        wall_clock.now = 9.5
        disc.inject('sim-1', '10.0.0.1', 8001)
        svc.tick_once()

        snap = svc.build_snapshot()
        assert snap['devices'][0]['connected'] is True
        assert snap['devices'][0]['last_seen'] == pytest.approx(9.5)

    def test_discovery_enabled_device_times_out_without_activity(self, caplog):
        fakes = [_FakeDevice()]
        fakes[0].is_connected = True
        fakes[0].ensure_connected = lambda: False
        clock = _FakeClock()
        wall_clock = _FakeWallClock(20.0)
        svc, _, _ = _make_discovery_service(
            n_devices=1,
            fake_devices=fakes,
            clock=clock,
            wall_clock=wall_clock,
        )

        assert svc.build_snapshot()['devices'][0]['last_seen'] == pytest.approx(20.0)

        clock.now = 6_500_000_001
        wall_clock.now = 27.0
        with caplog.at_level(logging.INFO):
            json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)

        status_events = [e for e in events if e.get('event') == 'device_status']
        assert len(status_events) == 1
        assert status_events[0]['connected'] is False
        assert status_events[0]['last_seen'] == pytest.approx(20.0)

        snap = svc.build_snapshot()
        assert snap['devices'][0]['connected'] is False
        assert snap['devices'][0]['last_seen'] == pytest.approx(20.0)
        assert (
            'device disconnected: id=1 uid=sim-1 type=sim strip=strip_a '
            'reason=heartbeat timeout last_seen=20.000 age_ms=6500.0'
        ) in caplog.text

    def test_heartbeat_timeout_log_includes_last_source_and_age(self, caplog):
        fakes = [_FakeDevice()]
        fakes[0].is_connected = True
        fakes[0].ensure_connected = lambda: False
        clock = _FakeClock()
        wall_clock = _FakeWallClock(20.0)
        svc, _, disc = _make_discovery_service(
            n_devices=1,
            fake_devices=fakes,
            clock=clock,
            wall_clock=wall_clock,
        )

        clock.now = 1_000_000_000
        wall_clock.now = 21.0
        disc.inject('sim-1', '10.0.0.1', 8001)
        svc.tick_once()

        clock.now = 7_500_000_001
        wall_clock.now = 28.0
        with caplog.at_level(logging.INFO):
            svc.tick_once()

        assert (
            'device disconnected: id=1 uid=sim-1 type=sim strip=strip_a '
            'host=10.0.0.1 tcp=8001 reason=heartbeat timeout '
            'last_seen=21.000 last_source=discovery age_ms=6500.0'
        ) in caplog.text

    def test_stale_disconnect_then_reconnect_emits_both_events_in_order(self, caplog):
        fakes = [_FakeDevice()]
        fakes[0].is_connected = True
        clock = _FakeClock()
        wall_clock = _FakeWallClock(11.0)
        svc, _, _ = _make_discovery_service(
            n_devices=1,
            fake_devices=fakes,
            clock=clock,
            wall_clock=wall_clock,
        )

        clock.now = 6_500_000_001
        wall_clock.now = 14.0
        with caplog.at_level(logging.INFO):
            json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)

        status_events = [e for e in events if e.get('event') == 'device_status']
        assert [(evt['source'], evt['connected']) for evt in status_events] == [
            ('connectivity', False),
            ('connectivity', True),
            ('reported', True),
        ]
        assert status_events[0]['last_seen'] == pytest.approx(11.0)
        assert status_events[1]['last_seen'] == pytest.approx(14.0)
        assert status_events[2]['last_seen'] == pytest.approx(14.0)

        snap = svc.build_snapshot()
        assert snap['devices'][0]['connected'] is True
        assert snap['devices'][0]['last_seen'] == pytest.approx(14.0)
        messages = [record.getMessage() for record in caplog.records if record.levelno == logging.INFO]
        lifecycle = [msg for msg in messages if msg.startswith('device ')]
        assert lifecycle == [
            'device disconnected: id=1 uid=sim-1 type=sim strip=strip_a reason=heartbeat timeout last_seen=11.000 age_ms=6500.0',
            'device connected: id=1 uid=sim-1 type=sim strip=strip_a',
        ]

    def test_no_event_when_stable(self, caplog):
        svc, fakes = _make_service()
        # All connected at init, stays connected
        with caplog.at_level(logging.INFO):
            json_msgs, _ = svc.tick_once()
            events = _decode_json_msgs(json_msgs)
            assert not any(e.get('event') == 'device_status' for e in events)

            json_msgs, _ = svc.tick_once()
            events = _decode_json_msgs(json_msgs)
            assert not any(e.get('event') == 'device_status' for e in events)
        assert 'device connected:' not in caplog.text
        assert 'device disconnected:' not in caplog.text

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

    def test_active_disconnect_detaches_runtime_same_tick(self):
        clock = _FakeClock()
        svc, fakes = _make_service(clock=clock)

        svc.handle_cmd({
            'id': 1, 'cmd': 'load',
            'source': _SIMPLE_DSL, 'beat': 1.0, 'duration': 5.0,
        })
        svc.handle_cmd({'id': 2, 'cmd': 'play'})
        svc.tick_once()  # drain initial load/play events

        def stay_disconnected():
            fakes[0].ensure_connected_calls += 1
            return False

        fakes[0].ensure_connected = stay_disconnected
        fakes[0].is_connected = False
        clock.now += 1_000_000_000
        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)

        detach_events = [
            e for e in events
            if e.get('event') == 'device_detached'
        ]
        status_indices = [
            i for i, e in enumerate(events)
            if e.get('event') == 'device_status'
        ]
        error_events = [e for e in events if e.get('event') == 'error']

        assert len(detach_events) == 1
        assert status_indices, 'expected device_status disconnect event'
        assert error_events == []
        assert detach_events[0]['device_id'] == 1
        assert detach_events[0]['reason'] == 'disconnected'
        assert detach_events[0]['session_role'] == 'detached'
        assert detach_events[0]['observer_suspended'] is True
        disconnect_status = next(
            e for e in events
            if e.get('event') == 'device_status' and e.get('source') == 'connectivity'
        )
        assert disconnect_status['session_role'] == 'detached'
        assert svc._controller.state == ControllerState.PLAYING
        assert svc._controller.uses_device(fakes[0]) is True
        assert fakes[0].close_calls == 1
        assert fakes[0]._state == DeviceState.IDLE
        assert fakes[0].ensure_connected_calls == 1

    def test_multiple_active_disconnects_detach_without_abort(self):
        config = Config(frame_port=1, devices=[
            DeviceConfig(1, 'sim-left', 'sim', '127.0.0.1', 9001, 'left', 5),
            DeviceConfig(2, 'sim-right', 'sim', '127.0.0.1', 9002, 'right', 5),
        ])
        fakes = [_FakeDevice(), _FakeDevice()]
        clock = _FakeClock()
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fakes),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir),
            clock=clock,
        )

        svc.handle_cmd({
            'id': 1, 'cmd': 'load',
            'source': _LEFT_RIGHT_DSL, 'beat': 1.0, 'duration': 5.0,
        })
        svc.handle_cmd({'id': 2, 'cmd': 'play'})
        svc.tick_once()  # drain initial load/play events

        for fake in fakes:
            fake.ensure_connected = lambda: False
            fake.is_connected = False
        clock.now += 1_000_000_000
        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)

        detach_events = [e for e in events if e.get('event') == 'device_detached']
        status_events = [e for e in events if e.get('event') == 'device_status']
        error_events = [e for e in events if e.get('event') == 'error']

        assert len(detach_events) == 2
        assert len(status_events) == 2
        assert error_events == []
        assert {e['device_id'] for e in detach_events} == {1, 2}
        assert svc._controller.state == ControllerState.PLAYING
        assert all(fake.close_calls == 1 for fake in fakes)


class TestProbeAllBaseline:
    def test_probe_all_does_not_cause_spurious_event(self):
        """probe_all() on client connect must not cause a spurious
        device_status event on the next tick_once()."""
        fakes = [_FakeDevice()]
        fakes[0].is_connected = False
        svc, _ = _make_service(fake_devices=fakes)

        # Simulate what ControllerServer does on client connect
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


class TestDeviceRejoin:
    def test_playing_sim_rejoins_after_reconnect(self):
        clock = _FakeClock()
        svc, fakes = _make_service(clock=clock)

        svc.handle_cmd({
            'id': 1, 'cmd': 'load',
            'source': _GAP_DSL, 'beat': 1.0, 'duration': 1.0,
        })
        svc.handle_cmd({'id': 2, 'cmd': 'play'})
        svc.tick_once()

        svc._last_probe_ns[1] = -1_000_000_000
        fakes[0].is_connected = False
        clock.now = 250_000_000
        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)

        assert [e['event'] for e in events if e.get('event') in (
            'device_status', 'device_detached', 'device_rejoined'
        )] == [
            'device_detached',
            'device_status',
            'device_status',
            'device_status',
            'device_rejoined',
        ]
        assert fakes[0].load_calls == 2
        assert fakes[0].jump_calls == 1
        assert fakes[0].resume_calls == 1
        assert svc._controller.is_device_attached(fakes[0]) is True
        assert svc._controller.is_device_serving(fakes[0]) is True

    def test_playing_esp_waits_for_sync_ready_before_rejoining(self):
        clock = _FakeClock()
        fake_sync = _FakeClockSyncManager()
        fakes = [_FakeDevice()]
        config = Config(frame_port=1, devices=[
            DeviceConfig(1, 'esp-1', 'esp32', '127.0.0.1', 9001, 'test', 5),
        ])
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fakes),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir),
            clock=clock,
            clock_sync_factory=lambda sync_port, clock_ns: fake_sync,
        )

        svc.handle_cmd({
            'id': 1, 'cmd': 'load',
            'source': _GAP_DSL, 'beat': 1.0, 'duration': 1.0,
        })
        svc.handle_cmd({'id': 2, 'cmd': 'play'})
        svc.tick_once()

        svc._last_probe_ns[1] = -1_000_000_000
        fakes[0].is_connected = False
        clock.now = 250_000_000
        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)

        assert [e for e in events if e.get('event') == 'device_rejoined'] == []
        assert svc._controller.is_device_attached(fakes[0]) is True
        assert svc._controller.is_device_serving(fakes[0]) is False

        fake_sync.pending_updates.append(SyncUpdate(
            device_id=1,
            clock_state='settling',
            clock_offset_us=None,
            rtt_us=1_000,
            send_correction=True,
            seq=7,
            boot_token=123,
            correction_offset_us=500,
        ))
        clock.now = 300_000_000
        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)

        rejoined = [e for e in events if e.get('event') == 'device_rejoined']
        assert len(rejoined) == 1
        assert fakes[0].sync_results == [(7, 123, 500)]
        assert fakes[0].load_calls == 2
        assert fakes[0].jump_calls == 1
        assert fakes[0].resume_calls == 1
        assert svc._controller.is_device_serving(fakes[0]) is True

    def test_paused_rejoin_loads_and_jumps_without_resume(self):
        clock = _FakeClock()
        svc, fakes = _make_service(clock=clock)

        svc.handle_cmd({
            'id': 1, 'cmd': 'load',
            'source': _SIMPLE_DSL, 'beat': 1.0, 'duration': 5.0,
        })
        svc.handle_cmd({'id': 2, 'cmd': 'play'})
        clock.now = 200_000_000
        svc.tick_once()
        svc.handle_cmd({'id': 3, 'cmd': 'pause'})
        svc.tick_once()

        svc._last_probe_ns[1] = -1_000_000_000
        fakes[0].is_connected = False
        clock.now = 300_000_000
        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)

        rejoined = [e for e in events if e.get('event') == 'device_rejoined']
        assert len(rejoined) == 1
        assert fakes[0].load_calls == 2
        assert fakes[0].jump_calls == 1
        assert fakes[0].resume_calls == 0
        assert svc._controller.state == ControllerState.PAUSED
        assert svc._controller.is_device_serving(fakes[0]) is True

    def test_loaded_rejoin_reloads_without_jump_or_resume(self):
        clock = _FakeClock()
        svc, fakes = _make_service(clock=clock)

        svc.handle_cmd({
            'id': 1, 'cmd': 'load',
            'source': _SIMPLE_DSL, 'beat': 1.0, 'duration': 5.0,
        })
        svc.tick_once()

        svc._last_probe_ns[1] = -1_000_000_000
        fakes[0].is_connected = False
        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)

        assert len([e for e in events if e.get('event') == 'device_rejoined']) == 1
        assert fakes[0].load_calls == 2
        assert fakes[0].jump_calls == 0
        assert fakes[0].resume_calls == 0
        assert fakes[0].start_calls == 0

    def test_stopped_rejoin_reloads_and_restores_stop_state(self):
        clock = _FakeClock()
        svc, fakes = _make_service(clock=clock)

        svc.handle_cmd({
            'id': 1, 'cmd': 'load',
            'source': _SIMPLE_DSL, 'beat': 1.0, 'duration': 5.0,
        })
        svc.handle_cmd({'id': 2, 'cmd': 'stop'})
        svc.tick_once()

        svc._last_probe_ns[1] = -1_000_000_000
        fakes[0].is_connected = False
        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)

        assert len([e for e in events if e.get('event') == 'device_rejoined']) == 1
        assert fakes[0].load_calls == 2
        assert fakes[0].stop_calls == 2
        assert svc._controller.state == ControllerState.STOPPED
        assert svc._controller.is_device_serving(fakes[0]) is True

    def test_ended_device_reconnect_does_not_rejoin(self):
        clock = _FakeClock()
        svc, fakes = _make_service(clock=clock)

        svc.handle_cmd({
            'id': 1, 'cmd': 'load',
            'source': _SIMPLE_DSL, 'beat': 1.0, 'duration': 0.5,
        })
        svc.handle_cmd({'id': 2, 'cmd': 'play'})
        svc.tick_once()

        clock.now = 600_000_000
        svc.tick_once()
        assert svc._controller.state == ControllerState.ENDED

        svc._last_probe_ns[1] = -1_000_000_000
        fakes[0].is_connected = False
        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)

        assert [e for e in events if e.get('event') == 'device_rejoined'] == []
        assert svc._controller.is_device_attached(fakes[0]) is True
        assert svc._controller.is_device_serving(fakes[0]) is False


class TestLastSeenReconciliation:
    def test_reconcile_preserves_last_seen_for_unchanged_devices(self):
        wall_clock = _FakeWallClock(42.0)
        svc, _ = _make_service(n_devices=2, wall_clock=wall_clock)

        svc.tick_once()
        assert svc.build_snapshot()['devices'][0]['last_seen'] == pytest.approx(42.0)

        candidate = copy.deepcopy(svc._config_to_doc(svc._config))
        new_config = load_config_obj(candidate)
        wall_clock.now = 100.0
        svc._reconcile_devices(new_config)

        snap = svc.build_snapshot()
        assert snap['devices'][0]['last_seen'] == pytest.approx(42.0)
        assert snap['devices'][1]['last_seen'] == pytest.approx(42.0)

    def test_reconcile_preserves_last_activity_source_for_unchanged_devices(self):
        clock = _FakeClock()
        wall_clock = _FakeWallClock(42.0)
        svc, fakes = _make_service(n_devices=2, clock=clock, wall_clock=wall_clock)

        fakes[0]._activity_observed = True
        svc.tick_once()
        assert svc._last_activity_source[1] == 'runtime'

        candidate = copy.deepcopy(svc._config_to_doc(svc._config))
        new_config = load_config_obj(candidate)
        clock.now = 1_000_000_000
        wall_clock.now = 100.0
        svc._reconcile_devices(new_config)

        assert svc._last_activity_source[1] == 'runtime'
        assert svc._last_activity_source[2] is None

    def test_reconcile_seeds_last_seen_for_already_connected_devices(self):
        wall_clock = _FakeWallClock(17.0)
        replacement = _FakeDevice()
        replacement.is_connected = True
        svc = ControllerService(
            Config(frame_port=1, devices=[
                DeviceConfig(1, 'sim-1', 'sim', '127.0.0.1', 9001, 'test', 5),
            ]),
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory([replacement]),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir),
            wall_clock=wall_clock,
        )

        snap = svc.build_snapshot()
        assert snap['devices'][0]['connected'] is True
        assert snap['devices'][0]['last_seen'] == pytest.approx(17.0)


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
# Controller protocol tests
# =========================================================================


class TestControllerProtocol:
    def test_json_round_trip(self):
        obj = {'foo': 'bar', 'n': 42}
        data = encode_json(obj)
        reader = ProtocolReader()
        reader.feed(data)
        msgs = reader.messages()
        assert len(msgs) == 1
        kind, payload = msgs[0]
        assert kind == KIND_JSON
        assert parse_json_payload(payload) == obj

    def test_partial_feed(self):
        obj = {'key': 'value'}
        data = encode_json(obj)
        reader = ProtocolReader()

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
        reader2 = ProtocolReader()
        for b in data:
            reader2.feed(bytes([b]))
        all_msgs = reader2.messages()
        assert len(all_msgs) == 1

    def test_multiple_messages(self):
        obj1 = {'a': 1}
        obj2 = {'b': 2}
        data = encode_json(obj1) + encode_json(obj2)
        reader = ProtocolReader()
        reader.feed(data)
        msgs = reader.messages()
        assert len(msgs) == 2
        assert parse_json_payload(msgs[0][1]) == obj1
        assert parse_json_payload(msgs[1][1]) == obj2

    def test_frame_encoding(self):
        from elemctl.controller_protocol import encode_frame
        rgb = b'\xff\x00\x00' * 5  # 5 red pixels
        data = encode_frame(42, 1.5, [rgb])
        reader = ProtocolReader()
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
        reader = ProtocolReader()
        reader.feed(bad + valid)
        msgs = reader.messages()
        assert len(msgs) == 1
        kind, payload = msgs[0]
        assert kind == KIND_JSON
        assert parse_json_payload(payload) == {'ok': True}


# =========================================================================
# Part B: Controller API integration tests
# =========================================================================


from .controller_helpers import ControllerClient, wait_for_socket as _wait_for_socket


def _json_dicts(messages):
    return [parse_json_payload(payload) for kind, payload in messages if kind == KIND_JSON]


def _snapshot_from_messages(messages):
    return next(msg for msg in _json_dicts(messages) if msg.get('event') == 'snapshot')


@pytest.fixture()
def controller_api(tmp_path):
    """Start a ControllerServer in a daemon thread, yield socket path."""
    socket_path = str(tmp_path / 'test.sock')
    config = _make_config()
    fakes = [_FakeDevice()]
    svc = ControllerService(
        config,
        receiver_factory=_NoopReceiver,
        device_factory=_make_fake_factory(fakes),
    )
    server = ControllerServer(svc, socket_path)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    assert _wait_for_socket(socket_path), 'server did not create socket'
    yield socket_path, server, fakes

    server.shutdown()
    thread.join(timeout=3.0)
    assert not thread.is_alive(), 'server thread did not exit after shutdown'


class TestControllerApiConnect:
    def test_connect_receives_snapshot(self, controller_api):
        socket_path, _, _ = controller_api
        client = ControllerClient(socket_path)
        try:
            msgs = client.recv_messages(timeout=1.0)
            snap = _snapshot_from_messages(msgs)
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
        server = ControllerServer(svc, socket_path)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        assert _wait_for_socket(socket_path)

        client = ControllerClient(socket_path)
        try:
            msgs = client.recv_messages(timeout=1.0)
            snap = _snapshot_from_messages(msgs)
            # Device was disconnected but probe_all ran on connect
            assert snap['devices'][0]['connected'] is True
            assert fakes[0].ensure_connected_calls >= 1
        finally:
            client.close()
            server.shutdown()
            thread.join(timeout=3.0)
            assert not thread.is_alive(), 'server thread did not exit'


class TestControllerApiLoadPlay:
    def test_load_play(self, controller_api):
        socket_path, _, _ = controller_api
        client = ControllerClient(socket_path)
        try:
            # Drain snapshot
            client.recv_messages(timeout=0.5)

            # Load
            client.send_cmd({
                'id': 1, 'cmd': 'load',
                'source': _SIMPLE_DSL, 'beat': 1.0, 'duration': 0.5,
            })
            msgs = client.recv_messages(timeout=1.0)
            replies = _json_dicts(msgs)
            load_reply = next(r for r in replies if r.get('type') == 'reply')
            assert load_reply['ok'] is True
            assert load_reply['result']['session_id'] == 1

            # Play
            client.send_cmd({'id': 2, 'cmd': 'play'})
            msgs = client.recv_messages(timeout=1.0)
            replies = _json_dicts(msgs)
            play_reply = next(r for r in replies if r.get('type') == 'reply')
            assert play_reply['ok'] is True
        finally:
            client.close()


class TestControllerApiShutdown:
    def test_shutdown_stops_server(self, tmp_path):
        socket_path = str(tmp_path / 'test.sock')
        config = _make_config()
        fakes = [_FakeDevice()]
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fakes),
        )
        server = ControllerServer(svc, socket_path)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        assert _wait_for_socket(socket_path)

        client = ControllerClient(socket_path)
        try:
            client.recv_messages(timeout=0.5)
            client.send_cmd({'id': 1, 'cmd': 'shutdown'})
            client.recv_messages(timeout=0.5)
        finally:
            client.close()

        thread.join(timeout=3.0)
        assert not thread.is_alive(), 'server thread did not exit'


class TestControllerApiReconnect:
    def test_reconnect_gets_fresh_snapshot(self, controller_api):
        socket_path, _, _ = controller_api
        # First connection — load something
        client1 = ControllerClient(socket_path)
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
        client2 = ControllerClient(socket_path)
        try:
            msgs = client2.recv_messages(timeout=1.0)
            snap = _snapshot_from_messages(msgs)
            assert snap['event'] == 'snapshot'
            assert snap['session'] is not None
            assert snap['session']['session_id'] == 1
        finally:
            client2.close()


class TestControllerApiRoles:
    def test_multiple_writers_allowed(self, controller_api):
        socket_path, _, _ = controller_api
        writer1 = ControllerClient(socket_path)
        writer2 = ControllerClient(socket_path)
        try:
            writer1.recv_messages(timeout=0.5)
            writer2.recv_messages(timeout=0.5)

            writer1.send_cmd({'id': 1, 'cmd': 'status'})
            writer2.send_cmd({'id': 2, 'cmd': 'status'})

            replies1 = _json_dicts(writer1.recv_messages(timeout=1.0))
            replies2 = _json_dicts(writer2.recv_messages(timeout=1.0))

            reply1 = next(r for r in replies1 if r.get('type') == 'reply' and r.get('id') == 1)
            reply2 = next(r for r in replies2 if r.get('type') == 'reply' and r.get('id') == 2)

            assert reply1['ok'] is True
            assert reply2['ok'] is True
        finally:
            writer2.close()
            writer1.close()

    def test_multiple_observers_allowed(self, controller_api):
        socket_path, _, _ = controller_api
        writer = ControllerClient(socket_path)
        observer1 = ControllerClient(socket_path, role=ROLE_OBSERVER)
        observer2 = ControllerClient(socket_path, role=ROLE_OBSERVER)
        try:
            snap1 = _snapshot_from_messages(observer1.recv_messages(timeout=1.0))
            snap2 = _snapshot_from_messages(observer2.recv_messages(timeout=1.0))
            assert snap1['event'] == 'snapshot'
            assert snap2['event'] == 'snapshot'
        finally:
            observer2.close()
            observer1.close()
            writer.close()

    def test_observer_command_rejected(self, controller_api):
        socket_path, _, _ = controller_api
        observer = ControllerClient(socket_path, role=ROLE_OBSERVER)
        try:
            observer.recv_messages(timeout=0.5)
            observer.send_cmd({'id': 7, 'cmd': 'status'})
            msgs = observer.recv_messages(timeout=1.0)
            replies = _json_dicts(msgs)
            reply = next(r for r in replies if r.get('type') == 'reply')
            assert reply['id'] == 7
            assert reply['ok'] is False
            assert reply['error'] == 'observer connections cannot send commands'
        finally:
            observer.close()

    def test_writer_disconnect_does_not_block_new_writer(self, controller_api):
        socket_path, _, _ = controller_api
        writer1 = ControllerClient(socket_path)
        writer1.close()
        time.sleep(0.1)

        writer2 = ControllerClient(socket_path)
        try:
            snap = _snapshot_from_messages(writer2.recv_messages(timeout=1.0))
            assert snap['event'] == 'snapshot'
        finally:
            writer2.close()

    def test_observer_connect_does_not_force_probe_all(self, tmp_path):
        socket_path = str(tmp_path / 'test.sock')
        config = _make_config()
        fakes = [_FakeDevice()]
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory(fakes),
        )
        probe_calls = 0
        real_probe_all = svc.probe_all

        def probe_all_spy():
            nonlocal probe_calls
            probe_calls += 1
            return real_probe_all()

        svc.probe_all = probe_all_spy
        server = ControllerServer(svc, socket_path)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        assert _wait_for_socket(socket_path)

        observer = ControllerClient(socket_path, role=ROLE_OBSERVER)
        try:
            observer.recv_messages(timeout=0.5)
            assert probe_calls == 0
        finally:
            observer.close()
            server.shutdown()
            thread.join(timeout=3.0)
            assert not thread.is_alive(), 'server thread did not exit'


# =========================================================================
# Discovery integration tests
# =========================================================================


class _FakeDiscovery:
    """Fake DiscoveryReceiver for service tests."""

    def __init__(self, port):
        self._pending: list[tuple[str, str, int, int]] = []
        self.sent_rejects: list[tuple[str, int, int]] = []

    def inject(self, uid: str, host: str, tcp_port: int, reply_port: int = 7000) -> None:
        self._pending.append((uid, host, tcp_port, reply_port))

    def poll(self) -> None:
        pass

    def drain_discoveries(self) -> list[tuple[str, str, int, int]]:
        out = self._pending[:]
        self._pending.clear()
        return out

    def send_reject(self, host: str, port: int, reason: int) -> None:
        self.sent_rejects.append((host, port, reason))

    def close(self) -> None:
        pass


def _make_discovery_service(
    n_devices=2,
    fake_devices=None,
    clock=None,
    wall_clock=None,
    clock_sync_factory=None,
):
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
    kwargs = dict(
        receiver_factory=_NoopReceiver,
        device_factory=_make_fake_factory(fake_devices),
        discovery_factory=lambda port: fake_disc,
    )
    if clock is not None:
        kwargs['clock'] = clock
    if wall_clock is not None:
        kwargs['wall_clock'] = wall_clock
    if clock_sync_factory is not None:
        kwargs['clock_sync_factory'] = clock_sync_factory
    svc = ControllerService(config, **kwargs)
    return svc, fake_devices, fake_disc


class TestDiscoveryIntegration:
    def test_discovery_promotes_provisional_esp32_short_uid(self, tmp_path):
        config = Config(
            frame_port=1,
            discovery_port=9999,
            devices=[
                DeviceConfig(
                    device_id=1,
                    device_uid='b5f190',
                    device_type='esp32',
                    host='',
                    tcp_port=0,
                    strip_id='main',
                    length=60,
                ),
            ],
        )
        created: list[_AddressableFakeDevice] = []

        def factory(device_id, host, tcp_port, device_type, strip_length, frame_port, udp_receiver):
            dev = _AddressableFakeDevice()
            dev.is_connected = False
            dev.ensure_connected = lambda: False
            created.append(dev)
            return dev

        fake_disc = _FakeDiscovery(9999)
        svc, path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=factory,
            discovery_factory=lambda port: fake_disc,
        )

        fake_disc.inject('esp32-246f28b5f190', '10.0.0.5', 8001)
        svc.tick_once()

        assert len(created) == 2
        assert created[0].close_calls == 1
        assert [dc.device_uid for dc in svc._device_configs] == ['esp32-246f28b5f190']
        assert svc._devices[0]._host == '10.0.0.5'
        assert svc._devices[0]._tcp_port == 8001

        saved = load_config(str(path))
        assert [dc.device_uid for dc in saved.devices] == ['esp32-246f28b5f190']

    def test_discovery_promotes_provisional_esp32_full_hex_uid(self, tmp_path):
        config = Config(
            frame_port=1,
            discovery_port=9999,
            devices=[
                DeviceConfig(
                    device_id=1,
                    device_uid='246f28b5f190',
                    device_type='esp32',
                    host='',
                    tcp_port=0,
                    strip_id='main',
                    length=60,
                ),
            ],
        )
        created: list[_AddressableFakeDevice] = []

        def factory(device_id, host, tcp_port, device_type, strip_length, frame_port, udp_receiver):
            dev = _AddressableFakeDevice()
            dev.is_connected = False
            dev.ensure_connected = lambda: False
            created.append(dev)
            return dev

        fake_disc = _FakeDiscovery(9999)
        svc, path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=factory,
            discovery_factory=lambda port: fake_disc,
        )

        fake_disc.inject('esp32-246f28b5f190', '10.0.0.5', 8001)
        svc.tick_once()

        assert len(created) == 2
        assert created[0].close_calls == 1
        assert [dc.device_uid for dc in svc._device_configs] == ['esp32-246f28b5f190']
        assert svc._devices[0]._host == '10.0.0.5'
        assert svc._devices[0]._tcp_port == 8001

        saved = load_config(str(path))
        assert [dc.device_uid for dc in saved.devices] == ['esp32-246f28b5f190']

    def test_discovery_defers_provisional_esp32_promotion_while_runtime_active(
        self, tmp_path, caplog
    ):
        config = Config(
            frame_port=1,
            discovery_port=9999,
            devices=[
                DeviceConfig(
                    device_id=1,
                    device_uid='b5f190',
                    device_type='esp32',
                    host='',
                    tcp_port=0,
                    strip_id='main',
                    length=60,
                ),
            ],
        )
        created: list[_AddressableFakeDevice] = []

        def factory(device_id, host, tcp_port, device_type, strip_length, frame_port, udp_receiver):
            dev = _AddressableFakeDevice()
            created.append(dev)
            return dev

        fake_disc = _FakeDiscovery(9999)
        svc, path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=factory,
            discovery_factory=lambda port: fake_disc,
        )

        source = """\
from elements.dsl import strip, spark, sec
s = strip('main', length=60)
sp = spark(color='white', fade=1.0)
sp.schedule(s.pixels('0-59'), at=0, duration=sec(0.5))
"""

        assert svc.handle_cmd({
            'id': 1, 'cmd': 'load',
            'source': source, 'beat': 1.0, 'duration': 0.5,
        })['ok'] is True
        assert svc.handle_cmd({'id': 2, 'cmd': 'play'})['ok'] is True
        assert svc._controller.state == ControllerState.PLAYING

        old_controller = svc._controller
        old_device = svc._devices[0]

        with caplog.at_level(logging.INFO):
            fake_disc.inject('esp32-246f28b5f190', '10.0.0.5', 8001)
            svc.tick_once()

        assert svc._controller is old_controller
        assert svc._devices[0] is old_device
        assert len(created) == 1
        assert created[0].close_calls == 0
        assert [dc.device_uid for dc in svc._device_configs] == ['b5f190']
        assert svc._discovery_cache['esp32-246f28b5f190'] == ('10.0.0.5', 8001)
        saved = load_config(str(path))
        assert [dc.device_uid for dc in saved.devices] == ['b5f190']
        assert (
            'discovery: deferring esp32 uid promotion until controller is quiescent: '
            'esp32-246f28b5f190'
        ) in caplog.text

    def test_discovery_leaves_ambiguous_provisional_esp32_uid_unpromoted(
        self,
        tmp_path,
        caplog,
    ):
        config = Config(
            frame_port=1,
            discovery_port=9999,
            devices=[
                DeviceConfig(
                    device_id=1,
                    device_uid='b5f190',
                    device_type='esp32',
                    host='',
                    tcp_port=0,
                    strip_id='main',
                    length=60,
                ),
                DeviceConfig(
                    device_id=2,
                    device_uid='246f28b5f190',
                    device_type='esp32',
                    host='',
                    tcp_port=0,
                    strip_id='aux',
                    length=30,
                ),
            ],
        )
        fake_disc = _FakeDiscovery(9999)
        svc, path = _make_service_with_path(
            tmp_path,
            config,
            device_factory=lambda *args, **kwargs: _AddressableFakeDevice(),
            discovery_factory=lambda port: fake_disc,
        )

        caplog.set_level(logging.ERROR)
        fake_disc.inject('esp32-246f28b5f190', '10.0.0.5', 8001)
        svc.tick_once()

        assert [dc.device_uid for dc in svc._device_configs] == ['b5f190', '246f28b5f190']
        saved = load_config(str(path))
        assert [dc.device_uid for dc in saved.devices] == ['b5f190', '246f28b5f190']
        assert 'ambiguous provisional esp32 uid' in caplog.text

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

    def test_discovery_ignores_conflicting_live_duplicate(self):
        fakes = [_FakeDevice()]
        fakes[0].is_connected = True
        fakes[0]._host = '10.0.0.1'
        fakes[0]._tcp_port = 8001
        address_updates = []

        def update_address(host, tcp_port):
            address_updates.append((host, tcp_port))

        fakes[0].update_address = update_address

        svc, _, disc = _make_discovery_service(n_devices=1, fake_devices=fakes)
        svc._discovery_cache['sim-1'] = ('10.0.0.1', 8001)
        svc._last_probe_ns[1] = 123

        # Discover with a different address
        disc.inject('sim-1', '10.0.0.2', 8002)
        svc.tick_once()
        assert address_updates == []
        assert disc.sent_rejects == [('10.0.0.2', 7000, 1)]
        assert svc._discovery_cache['sim-1'] == ('10.0.0.1', 8001)
        assert svc._last_probe_ns[1] == 123
        assert svc.build_snapshot()['devices'][0]['connected'] is True

    def test_connected_device_without_known_endpoint_accepts_discovery(self):
        fakes = [_FakeDevice()]
        fakes[0].is_connected = True
        address_updates = []

        def update_address(host, tcp_port):
            address_updates.append((host, tcp_port))
            return True

        fakes[0].update_address = update_address

        svc, _, disc = _make_discovery_service(n_devices=1, fake_devices=fakes)

        disc.inject('sim-1', '10.0.0.2', 8002)
        svc.tick_once()
        assert address_updates == [('10.0.0.2', 8002)]
        assert disc.sent_rejects == []

    def test_alternating_duplicate_hello_does_not_override_live_uid(self):
        fakes = [_FakeDevice()]
        fakes[0].is_connected = True
        fakes[0]._host = '10.0.0.1'
        fakes[0]._tcp_port = 8001
        address_updates = []

        def update_address(host, tcp_port):
            address_updates.append((host, tcp_port))

        fakes[0].update_address = update_address

        svc, _, disc = _make_discovery_service(n_devices=1, fake_devices=fakes)
        svc._discovery_cache['sim-1'] = ('10.0.0.1', 8001)
        svc._last_probe_ns[1] = 123

        disc.inject('sim-1', '10.0.0.2', 8002)
        svc.tick_once()
        disc.inject('sim-1', '10.0.0.3', 8003)
        svc.tick_once()

        assert address_updates == []
        assert disc.sent_rejects == [
            ('10.0.0.2', 7000, 1),
            ('10.0.0.3', 7000, 1),
        ]
        assert svc._discovery_cache['sim-1'] == ('10.0.0.1', 8001)
        assert svc._last_probe_ns[1] == 123
        assert svc.build_snapshot()['devices'][0]['connected'] is True

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


class TestBackgroundProvisioning:
    def test_provision_background_uses_retained_session_artifact(self):
        config = Config(frame_port=1, devices=[
            DeviceConfig(1, 'esp-1', 'esp32', '127.0.0.1', 9001, 'main', 10),
        ])
        fake = _FakeDevice()
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory([fake]),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir),
        )

        manifest = CompiledManifest(
            duration=1.0,
            strips=[CompiledStripArtifact('main', 10, b'123456789')],
            safe_intervals=[],
        )
        assert svc._controller.load(manifest) is True

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'provision_background',
            'device_uid': 'esp-1',
        })

        assert reply['ok'] is True
        assert reply['result'] == {
            'device_uid': 'esp-1',
            'strip_length': 10,
            'blob_len': 9,
            'crc32': 0xCBF43926,
        }
        assert fake.store_background_calls == [(b'123456789', 10, 0xCBF43926)]

    def test_provision_background_rejects_strip_length_mismatch(self):
        config = Config(frame_port=1, devices=[
            DeviceConfig(1, 'esp-1', 'esp32', '127.0.0.1', 9001, 'main', 10),
        ])
        fake = _FakeDevice()
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory([fake]),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir),
        )

        manifest = CompiledManifest(
            duration=1.0,
            strips=[CompiledStripArtifact('main', 5, b'123456789')],
            safe_intervals=[],
        )
        assert svc._controller.load(manifest) is True

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'provision_background',
            'device_uid': 'esp-1',
        })

        assert reply['ok'] is False
        assert 'does not match device configured length' in reply['error']
        assert fake.store_background_calls == []

    def test_provision_background_rejects_device_outside_current_session(self):
        config = Config(frame_port=1, devices=[
            DeviceConfig(1, 'esp-1', 'esp32', '127.0.0.1', 9001, 'main', 10),
        ])
        fake = _FakeDevice()
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory([fake]),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir),
        )

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'provision_background',
            'device_uid': 'esp-1',
        })

        assert reply['ok'] is False
        assert 'current retained session' in reply['error']

    def test_clear_background_dispatches_to_device(self):
        config = Config(frame_port=1, devices=[
            DeviceConfig(1, 'esp-1', 'esp32', '127.0.0.1', 9001, 'main', 10),
        ])
        fake = _FakeDevice()
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory([fake]),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir),
        )

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'clear_background',
            'device_uid': 'esp-1',
        })

        assert reply['ok'] is True
        assert reply['result'] == {'device_uid': 'esp-1'}
        assert fake.clear_background_calls == 1


class TestReportedDeviceStatus:
    def test_snapshot_includes_session_role_and_observer_state(self):
        clock = _FakeClock()
        svc, fakes = _make_service(fake_devices=[_FrameProducingDevice()], clock=clock)

        svc.handle_cmd({
            'id': 1, 'cmd': 'load',
            'source': _SIMPLE_DSL, 'beat': 1.0, 'duration': 5.0,
        })

        snap = svc.build_snapshot()
        assert snap['devices'][0]['session_role'] == 'serving'
        assert snap['session']['observer_suspended'] is False
        assert snap['session']['strips'][0]['targets'][0]['session_role'] == 'serving'

        def stay_disconnected():
            fakes[0].ensure_connected_calls += 1
            return False

        fakes[0].ensure_connected = stay_disconnected
        fakes[0].is_connected = False
        clock.now += 1_000_000_000
        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)

        disconnect_status = next(
            e for e in events
            if e.get('event') == 'device_status' and e.get('source') == 'connectivity'
        )
        detach_event = next(e for e in events if e.get('event') == 'device_detached')
        assert disconnect_status['session_role'] == 'detached'
        assert detach_event['session_role'] == 'detached'
        assert detach_event['observer_suspended'] is True

        snap = svc.build_snapshot()
        assert snap['devices'][0]['session_role'] == 'detached'
        assert snap['session']['observer_suspended'] is True
        assert snap['session']['strips'][0]['targets'][0]['session_role'] == 'detached'

    def test_query_device_status_command_updates_snapshot_and_emits_event(self):
        wall_clock = _FakeWallClock(42.0)
        svc, fakes = _make_service(wall_clock=wall_clock)

        reply = svc.handle_cmd({
            'id': 1,
            'cmd': 'query_device_status',
            'device_uid': 'sim-1',
        })

        assert reply['ok'] is True
        assert reply['result'] == fakes[0].reported_status
        assert fakes[0].query_device_status_calls == 1

        snap = svc.build_snapshot()
        assert snap['devices'][0]['reported'] == fakes[0].reported_status
        assert snap['devices'][0]['reported_at'] == pytest.approx(42.0)

        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)
        reported = [e for e in events if e.get('event') == 'device_status' and e.get('source') == 'reported']
        assert len(reported) == 1
        assert reported[0]['reported'] == fakes[0].reported_status

    def test_connect_transition_queries_device_status(self):
        fake = _FakeDevice()
        fake.is_connected = False
        wall_clock = _FakeWallClock(10.5)
        svc, _ = _make_service(fake_devices=[fake], wall_clock=wall_clock)

        json_msgs, _ = svc.tick_once()
        events = _decode_json_msgs(json_msgs)

        assert fake.query_device_status_calls == 1
        snap = svc.build_snapshot()
        assert snap['devices'][0]['connected'] is True
        assert snap['devices'][0]['reported'] == fake.reported_status
        assert snap['devices'][0]['reported_at'] == pytest.approx(10.5)
        assert len([
            e for e in events
            if e.get('event') == 'device_status' and e.get('source') == 'reported'
        ]) == 1

    def test_disconnect_keeps_last_reported_state(self):
        wall_clock = _FakeWallClock(5.0)
        svc, fakes = _make_service(wall_clock=wall_clock)
        assert svc.handle_cmd({
            'id': 1,
            'cmd': 'query_device_status',
            'device_uid': 'sim-1',
        })['ok'] is True

        fakes[0].is_connected = False
        fakes[0].ensure_connected = lambda: False
        wall_clock.now = 9.0
        svc.tick_once()

        snap = svc.build_snapshot()
        assert snap['devices'][0]['connected'] is False
        assert snap['devices'][0]['reported'] == fakes[0].reported_status
        assert snap['devices'][0]['reported_at'] == pytest.approx(5.0)

    def test_background_commands_update_cached_report(self):
        config = Config(frame_port=1, devices=[
            DeviceConfig(1, 'esp-1', 'esp32', '127.0.0.1', 9001, 'main', 10),
        ])
        fake = _FakeDevice()
        fake.reported_status = {
            'mode': 'attached_controlled',
            'profile_present': True,
            'profile_strip_length': 10,
            'background_present': False,
            'background_strip_length': 0,
            'background_blob_len': 0,
            'background_crc32': 0,
        }
        wall_clock = _FakeWallClock(7.0)
        svc = ControllerService(
            config,
            receiver_factory=_NoopReceiver,
            device_factory=_make_fake_factory([fake]),
            library_factory=lambda animations_dir: _FakeLibrary(animations_dir),
            wall_clock=wall_clock,
        )

        assert svc.handle_cmd({
            'id': 1,
            'cmd': 'query_device_status',
            'device_uid': 'esp-1',
        })['ok'] is True

        manifest = CompiledManifest(
            duration=1.0,
            strips=[CompiledStripArtifact('main', 10, b'123456789')],
            safe_intervals=[],
        )
        assert svc._controller.load(manifest) is True

        wall_clock.now = 8.0
        assert svc.handle_cmd({
            'id': 2,
            'cmd': 'provision_background',
            'device_uid': 'esp-1',
        })['ok'] is True
        snap = svc.build_snapshot()
        assert snap['devices'][0]['reported']['background_present'] is True
        assert snap['devices'][0]['reported']['background_strip_length'] == 10
        assert snap['devices'][0]['reported']['background_blob_len'] == 9
        assert snap['devices'][0]['reported']['background_crc32'] == 0xCBF43926
        assert snap['devices'][0]['reported_at'] == pytest.approx(8.0)

        wall_clock.now = 9.0
        assert svc.handle_cmd({
            'id': 3,
            'cmd': 'clear_background',
            'device_uid': 'esp-1',
        })['ok'] is True
        snap = svc.build_snapshot()
        assert snap['devices'][0]['reported']['background_present'] is False
        assert snap['devices'][0]['reported']['background_strip_length'] == 0
        assert snap['devices'][0]['reported']['background_blob_len'] == 0
        assert snap['devices'][0]['reported']['background_crc32'] == 0
        assert snap['devices'][0]['reported_at'] == pytest.approx(9.0)


class TestControllerApiFrameDelivery:
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
        server = ControllerServer(svc, socket_path)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        assert _wait_for_socket(socket_path)

        client = ControllerClient(socket_path)
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
