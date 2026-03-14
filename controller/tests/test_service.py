"""Tests for ControllerService and UdsServer."""

import copy
import hashlib
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

from elemctl.config import Config, DeviceConfig, load_config, load_config_obj
from elemctl.controller import ControllerState
from elemctl.device import DeviceState
from elemctl.library import ProgramEntry
from elemctl.program_metadata import extract_metadata
from elemctl.service import ControllerService
from elemctl.server import UdsServer
from elemctl.uds_wire import (
    KIND_FRAME,
    KIND_JSON,
    PROTOCOL_VERSION,
    ROLE_OBSERVER,
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


class _AddressableFakeDevice(_FakeDevice):
    def __init__(self):
        super().__init__()
        self._host = ''
        self._tcp_port = 0
        self.close_calls = 0

    def update_address(self, host, tcp_port):
        changed = host != self._host or tcp_port != self._tcp_port
        self._host = host
        self._tcp_port = tcp_port
        return changed

    def close(self):
        self.close_calls += 1
        self.is_connected = False
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
            entry = ProgramEntry(
                program_id=program_id,
                path=f'/tmp/{program_id}.py',
                source=source,
                source_hash=source_hash,
                beat=beat,
                duration=duration,
                error=None,
            )
        except ValueError as e:
            entry = ProgramEntry(
                program_id=program_id,
                path=f'/tmp/{program_id}.py',
                source=source,
                source_hash=source_hash,
                beat=None,
                duration=None,
                error=str(e),
            )

        self._entries = [
            existing for existing in self._entries
            if existing.program_id != program_id
        ]
        self._entries.append(entry)
        return entry


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


def _make_program_entry(
    program_id: str,
    *,
    source: str = _SIMPLE_DSL,
    beat: float = 1.0,
    duration: float = 0.5,
    error: str | None = None,
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
    )


class _FakeClock:
    def __init__(self):
        self.now = 0

    def __call__(self):
        return self.now


def _make_service(n_devices=1, fake_devices=None, clock=None, library_factory=None):
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
    if clock is not None:
        kwargs['clock'] = clock
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

    def test_publish_program_broken_source_is_stored_with_error(self):
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

        assert reply['ok'] is True
        assert reply['result'] == {
            'program': {
                'program_id': 'broken',
                'beat': None,
                'duration': None,
                'error': 'missing DURATION',
            }
        }

        load_reply = svc.handle_cmd({
            'id': 2,
            'cmd': 'load_program',
            'program_id': 'broken',
        })
        assert load_reply['ok'] is False
        assert load_reply['error'] == 'program broken is not loadable: missing DURATION'

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


class TestConfigMutations:
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

        assert reply['ok'] is False
        assert reply['error'] == 'strip id already exists: strip_b'

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
        assert 'idle or stopped' in reply['error']

        reply2 = svc.handle_cmd({
            'id': 3,
            'cmd': 'edit_device',
            'target_device_uid': 'sim-1',
            'device_uid': 'sim-1',
            'strip_id': 'test',
            'length': 8,
        })

        assert reply2['ok'] is False
        assert 'idle or stopped' in reply2['error']

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
            'source': _SIMPLE_DSL,
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


def _json_dicts(messages):
    return [parse_json_payload(payload) for kind, payload in messages if kind == KIND_JSON]


def _snapshot_from_messages(messages):
    return next(msg for msg in _json_dicts(messages) if msg.get('event') == 'snapshot')


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
        server = UdsServer(svc, socket_path)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        assert _wait_for_socket(socket_path)

        client = UdsClient(socket_path)
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
            snap = _snapshot_from_messages(msgs)
            assert snap['event'] == 'snapshot'
            assert snap['session'] is not None
            assert snap['session']['session_id'] == 1
        finally:
            client2.close()


class TestUdsRoles:
    def test_second_writer_rejected(self, uds_service):
        socket_path, _, _ = uds_service
        writer = UdsClient(socket_path)
        try:
            with pytest.raises(ConnectionError, match='writer role already in use'):
                UdsClient(socket_path)
        finally:
            writer.close()

    def test_multiple_observers_allowed(self, uds_service):
        socket_path, _, _ = uds_service
        writer = UdsClient(socket_path)
        observer1 = UdsClient(socket_path, role=ROLE_OBSERVER)
        observer2 = UdsClient(socket_path, role=ROLE_OBSERVER)
        try:
            snap1 = _snapshot_from_messages(observer1.recv_messages(timeout=1.0))
            snap2 = _snapshot_from_messages(observer2.recv_messages(timeout=1.0))
            assert snap1['event'] == 'snapshot'
            assert snap2['event'] == 'snapshot'
        finally:
            observer2.close()
            observer1.close()
            writer.close()

    def test_observer_command_rejected(self, uds_service):
        socket_path, _, _ = uds_service
        observer = UdsClient(socket_path, role=ROLE_OBSERVER)
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

    def test_writer_disconnect_frees_slot(self, uds_service):
        socket_path, _, _ = uds_service
        writer1 = UdsClient(socket_path)
        writer1.close()
        time.sleep(0.1)

        writer2 = UdsClient(socket_path)
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
        server = UdsServer(svc, socket_path)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        assert _wait_for_socket(socket_path)

        observer = UdsClient(socket_path, role=ROLE_OBSERVER)
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
