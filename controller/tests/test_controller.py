import pytest

from elements.types import CompiledManifest, CompiledStripArtifact

from elemctl.controller import (
    Controller,
    ControllerEvent,
    ControllerState,
    ProgramFrame,
    StripConfig,
)
from elemctl.device import DeviceFrame, DeviceState


# ---------------------------------------------------------------------------
# MockDevice — deterministic sim device for testing
# ---------------------------------------------------------------------------


class MockDevice:
    """Minimal device that tracks state and produces frames deterministically."""

    def __init__(self, duration: float = 5.0, strip_length: int = 5):
        self._state = DeviceState.IDLE
        self._gen = 0
        self._frame_index = 0
        self._t0_ns = 0
        self._duration_sec = duration
        self._strip_length = strip_length
        self._last_t_rel = 0.0
        self._frames: list[DeviceFrame] = []
        self.load_should_fail = False
        self.load_calls = 0
        self.start_calls = 0
        self.jump_calls = 0
        self.pause_calls = 0
        self.resume_calls = 0
        self.stop_calls = 0
        self.tick_calls = 0

    def load(self, blob: bytes, gen: int) -> bool:
        self.load_calls += 1
        if self.load_should_fail:
            self._state = DeviceState.IDLE
            return False
        self._state = DeviceState.LOADED
        self._gen = gen
        self._frame_index = 0
        self._last_t_rel = 0.0
        return True

    def start(self, t0_ns: int) -> None:
        self.start_calls += 1
        self._t0_ns = t0_ns
        self._frame_index = 0
        self._frames.clear()
        self._state = DeviceState.PLAYING

    def jump(self, t0_ns: int, t_rel: float, gen: int) -> None:
        self.jump_calls += 1
        self._t0_ns = t0_ns
        self._gen = gen
        self._frame_index = 0
        self._frames.clear()
        if self._state == DeviceState.PLAYING:
            pass  # stay PLAYING
        else:
            # LOADED, PAUSED, ENDED → render one frame and pause
            self._last_t_rel = t_rel
            self._frames.append(DeviceFrame(
                gen=self._gen,
                frame_index=0,
                t_rel=t_rel,
                rgb=b'\x00' * (self._strip_length * 3),
            ))
            self._frame_index = 1
            self._state = DeviceState.PAUSED

    def pause(self, now_ns: int) -> None:
        self.pause_calls += 1
        if self._state == DeviceState.PLAYING:
            self._last_t_rel = (now_ns - self._t0_ns) / 1e9
            self._state = DeviceState.PAUSED

    def resume(self, t0_ns: int) -> None:
        self.resume_calls += 1
        self._t0_ns = t0_ns
        self._state = DeviceState.PLAYING

    def stop(self) -> None:
        self.stop_calls += 1
        if self._state != DeviceState.IDLE:
            self._state = DeviceState.LOADED
            self._last_t_rel = 0.0

    def tick_once(self, now_ns: int) -> None:
        self.tick_calls += 1
        if self._state != DeviceState.PLAYING:
            return
        t_rel = (now_ns - self._t0_ns) / 1e9
        if t_rel >= self._duration_sec:
            self._state = DeviceState.ENDED
            self._last_t_rel = self._duration_sec
            return
        self._last_t_rel = t_rel
        self._frames.append(DeviceFrame(
            gen=self._gen,
            frame_index=self._frame_index,
            t_rel=t_rel,
            rgb=b'\x00' * (self._strip_length * 3),
        ))
        self._frame_index += 1

    def state(self) -> DeviceState:
        return self._state

    def current_t_rel(self, now_ns: int) -> float:
        if self._state == DeviceState.PLAYING:
            return (now_ns - self._t0_ns) / 1e9
        return self._last_t_rel

    def drain_frames(self) -> list[DeviceFrame]:
        out = self._frames[:]
        self._frames.clear()
        return out

    def supports_debug_seek(self) -> bool:
        return True

    def debug_seek(self, t_rel: float, now_ns: int) -> None:
        t_rel = max(0.0, min(t_rel, self._duration_sec))
        self._last_t_rel = t_rel
        self._frame_index = 0
        self._frames.clear()
        self._frames.append(DeviceFrame(
            gen=self._gen,
            frame_index=0,
            t_rel=t_rel,
            rgb=b'\x00' * (self._strip_length * 3),
        ))
        self._frame_index = 1
        if self._state == DeviceState.PLAYING:
            self._t0_ns = now_ns - int(t_rel * 1e9)
        else:
            self._state = DeviceState.PAUSED


# ---------------------------------------------------------------------------
# FakeDevice — device without debug_seek support
# ---------------------------------------------------------------------------


class FakeDevice:
    """Device without debug_seek support (simulates real hardware)."""

    def __init__(self):
        self._state = DeviceState.IDLE
        self._gen = 0
        self._last_t_rel = 0.0

    def load(self, blob: bytes, gen: int) -> bool:
        self._state = DeviceState.LOADED
        self._gen = gen
        return True

    def start(self, t0_ns: int) -> None:
        self._state = DeviceState.PLAYING

    def jump(self, t0_ns: int, t_rel: float, gen: int) -> None:
        self._gen = gen
        if self._state != DeviceState.PLAYING:
            self._last_t_rel = t_rel
            self._state = DeviceState.PAUSED

    def pause(self, now_ns: int) -> None:
        if self._state == DeviceState.PLAYING:
            self._state = DeviceState.PAUSED

    def resume(self, t0_ns: int) -> None:
        self._state = DeviceState.PLAYING

    def stop(self) -> None:
        if self._state != DeviceState.IDLE:
            self._state = DeviceState.LOADED

    def tick_once(self, now_ns: int) -> None:
        pass

    def state(self) -> DeviceState:
        return self._state

    def current_t_rel(self, now_ns: int) -> float:
        return self._last_t_rel

    def drain_frames(self) -> list[DeviceFrame]:
        return []

    def supports_debug_seek(self) -> bool:
        return False

    def debug_seek(self, t_rel: float, now_ns: int) -> None:
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sec(t: float) -> int:
    """Convert seconds to nanoseconds."""
    return int(t * 1e9)


def has_event(evts: list[ControllerEvent], kind: ControllerEvent.Kind) -> bool:
    return any(e.kind == kind for e in evts)


def has_state_event(evts: list[ControllerEvent], state: ControllerState) -> bool:
    return any(
        e.kind == ControllerEvent.Kind.STATE_CHANGED and e.state == state
        for e in evts
    )


class DualFixture:
    def __init__(self):
        self.left = MockDevice(duration=5.0)
        self.right = MockDevice(duration=5.0)
        self._now_ns = 0

    def clock(self) -> int:
        return self._now_ns

    def set_time(self, seconds: float):
        self._now_ns = _sec(seconds)

    def strips(self) -> list[StripConfig]:
        return [
            StripConfig("left", 5, self.left),
            StripConfig("right", 5, self.right),
        ]

    def manifest(self) -> CompiledManifest:
        return CompiledManifest(
            duration=5.0,
            strips=[
                CompiledStripArtifact("left", 5, b'\x00'),
                CompiledStripArtifact("right", 5, b'\x00'),
            ],
            safe_intervals=[],
        )


class MirrorFixture:
    def __init__(self):
        self.sim = MockDevice(duration=5.0)
        self.esp = MockDevice(duration=5.0)
        self.bench = MockDevice(duration=5.0)
        self._now_ns = 0

    def clock(self) -> int:
        return self._now_ns

    def set_time(self, seconds: float):
        self._now_ns = _sec(seconds)

    def strips(self) -> list[StripConfig]:
        return [
            StripConfig("main", 5, self.sim),
            StripConfig("main", 5, self.esp),
            StripConfig("bench", 5, self.bench),
        ]

    def manifest(self) -> CompiledManifest:
        return CompiledManifest(
            duration=5.0,
            strips=[CompiledStripArtifact("main", 5, b'\x00')],
            safe_intervals=[],
        )


class MirroredDualFixture:
    def __init__(self):
        self.left_sim = MockDevice(duration=5.0)
        self.left_esp = MockDevice(duration=5.0)
        self.right_sim = MockDevice(duration=5.0)
        self.right_esp = MockDevice(duration=5.0)
        self._now_ns = 0

    def clock(self) -> int:
        return self._now_ns

    def set_time(self, seconds: float):
        self._now_ns = _sec(seconds)

    def strips(self) -> list[StripConfig]:
        return [
            StripConfig("left", 5, self.left_sim),
            StripConfig("left", 5, self.left_esp),
            StripConfig("right", 5, self.right_sim),
            StripConfig("right", 5, self.right_esp),
        ]

    def manifest(self) -> CompiledManifest:
        return CompiledManifest(
            duration=5.0,
            strips=[
                CompiledStripArtifact("left", 5, b'\x00'),
                CompiledStripArtifact("right", 5, b'\x00'),
            ],
            safe_intervals=[],
        )


# =========================================================================
# 1. Load and validation
# =========================================================================


class TestLoad:
    def test_successful_load(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        assert ctrl.state == ControllerState.IDLE
        assert ctrl.load(f.manifest())

        assert ctrl.state == ControllerState.LOADED
        assert ctrl.session_id == 1
        assert ctrl.epoch == 0
        assert ctrl.duration == pytest.approx(5.0)

        evts = ctrl.drain_events()
        assert has_event(evts, ControllerEvent.Kind.SESSION_STARTED)
        assert has_state_event(evts, ControllerState.LOADED)

    def test_successive_loads_increment_session_id(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        assert ctrl.load(f.manifest())
        assert ctrl.session_id == 1

        assert ctrl.load(f.manifest())
        assert ctrl.session_id == 2

    def test_unknown_strip_id_fails(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        m = f.manifest()
        m.strips[0] = CompiledStripArtifact("wrong", 5, b'\x00')
        assert not ctrl.load(m)

        assert ctrl.state == ControllerState.IDLE
        assert has_event(ctrl.drain_events(), ControllerEvent.Kind.ERROR)

    def test_program_longer_than_configured_strip_fails(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        m = f.manifest()
        m.strips[0] = CompiledStripArtifact("left", 10, b'\x00')
        assert not ctrl.load(m)
        assert has_event(ctrl.drain_events(), ControllerEvent.Kind.ERROR)

    def test_program_shorter_than_configured_strip_is_allowed(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        m = f.manifest()
        m.strips[0] = CompiledStripArtifact("left", 3, b'\x00')
        assert ctrl.load(m)
        assert ctrl.state == ControllerState.LOADED

    def test_duplicate_strip_id_fails(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        m = CompiledManifest(
            duration=5.0,
            strips=[
                CompiledStripArtifact("left", 5, b'\x00'),
                CompiledStripArtifact("left", 5, b'\x00'),
            ],
            safe_intervals=[],
        )
        assert not ctrl.load(m)
        assert has_event(ctrl.drain_events(), ControllerEvent.Kind.ERROR)

    def test_strip_count_mismatch_fails(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        m = CompiledManifest(
            duration=5.0,
            strips=[CompiledStripArtifact("left", 5, b'\x00')],
            safe_intervals=[],
        )
        assert not ctrl.load(m)
        assert has_event(ctrl.drain_events(), ControllerEvent.Kind.ERROR)

    def test_device_load_failure(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        f.right.load_should_fail = True
        assert not ctrl.load(f.manifest())

        assert ctrl.state == ControllerState.IDLE
        assert ctrl.session_id == 0  # identity not mutated
        assert has_event(ctrl.drain_events(), ControllerEvent.Kind.ERROR)

        # Left device was loaded OK then stopped; right failed
        assert f.left.state() == DeviceState.LOADED
        assert f.right.state() == DeviceState.IDLE

    def test_failed_device_load_does_not_advance_identity(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        # Successful first load
        assert ctrl.load(f.manifest())
        assert ctrl.session_id == 1
        ctrl.drain_events()

        # Play to set epoch > 0
        ctrl.play()
        assert ctrl.epoch == 1
        ctrl.drain_events()

        # Failed second load
        f.right.load_should_fail = True
        assert not ctrl.load(f.manifest())

        assert ctrl.session_id == 1  # not advanced
        assert ctrl.epoch == 1       # not advanced
        assert ctrl.state == ControllerState.IDLE
        assert ctrl.duration == 0.0
        assert ctrl.current_t_rel == 0.0
        assert ctrl.drain_program_frames() == []

    def test_failed_reload_clears_session_remnants(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        # Successful load + play
        assert ctrl.load(f.manifest())
        f.set_time(0)
        ctrl.play()
        ctrl.drain_events()

        # Tick to produce frames
        f.set_time(0.5)
        ctrl.tick_once()
        assert ctrl.drain_program_frames()

        assert ctrl.duration == pytest.approx(5.0)

        # Failed reload
        f.right.load_should_fail = True
        assert not ctrl.load(f.manifest())

        assert ctrl.state == ControllerState.IDLE
        assert ctrl.duration == pytest.approx(0.0)
        assert ctrl.current_t_rel == pytest.approx(0.0)
        assert ctrl.drain_program_frames() == []

        # Recover
        f.right.load_should_fail = False
        assert ctrl.load(f.manifest())
        assert ctrl.drain_program_frames() == []  # no stale frames

    def test_order_independent_strip_matching(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        # Manifest with reversed strip order
        m = CompiledManifest(
            duration=5.0,
            strips=[
                CompiledStripArtifact("right", 5, b'\x00'),
                CompiledStripArtifact("left", 5, b'\x00'),
            ],
            safe_intervals=[],
        )
        assert ctrl.load(m)
        assert ctrl.state == ControllerState.LOADED

    def test_targeted_load_fans_out_single_strip(self):
        f = MirrorFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        assert ctrl.load(f.manifest(), target_groups=[[0, 1]])
        assert ctrl.state == ControllerState.LOADED
        assert f.sim.load_calls == 1
        assert f.esp.load_calls == 1
        assert f.bench.load_calls == 0
        assert ctrl.session_strips == [('main', 5)]

    def test_targeted_load_operates_only_on_active_targets(self):
        f = MirrorFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        assert ctrl.load(f.manifest(), target_groups=[[0, 1]])
        ctrl.drain_events()

        f.set_time(0.0)
        ctrl.play()
        assert f.sim.start_calls == 1
        assert f.esp.start_calls == 1
        assert f.bench.start_calls == 0

        f.set_time(0.25)
        ctrl.tick_once()
        assert f.sim.tick_calls > 0
        assert f.esp.tick_calls > 0
        assert f.bench.tick_calls > 0

        ctrl.pause()
        assert f.sim.pause_calls == 1
        assert f.esp.pause_calls == 1
        assert f.bench.pause_calls == 0

        ctrl.stop()
        assert f.sim.stop_calls >= 1
        assert f.esp.stop_calls >= 1
        assert f.bench.stop_calls == 0

    def test_targeted_load_stops_stale_previous_targets_after_success(self):
        f = MirrorFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        assert ctrl.load(f.manifest(), target_groups=[[0]])
        f.set_time(0.0)
        ctrl.play()
        ctrl.drain_events()
        assert f.sim.start_calls == 1
        assert f.esp.start_calls == 0

        assert ctrl.load(f.manifest(), target_groups=[[1]])
        assert ctrl.state == ControllerState.LOADED
        assert f.sim.stop_calls >= 1
        assert f.esp.load_calls == 1

    def test_targeted_load_emits_logical_frames_for_mirrored_dual_program(self):
        f = MirroredDualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        assert ctrl.load(f.manifest(), target_groups=[[0, 1], [2, 3]])
        ctrl.drain_events()

        f.set_time(0.0)
        ctrl.play()
        ctrl.drain_events()

        f.set_time(0.25)
        ctrl.tick_once()
        frames = ctrl.drain_program_frames()
        assert len(frames) == 1
        assert isinstance(frames[0], ProgramFrame)
        assert len(frames[0].strips) == 2
        assert ctrl.session_strips == [('left', 5), ('right', 5)]


# =========================================================================
# 2. Playback
# =========================================================================


class TestPlayback:
    def test_play_from_loaded(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        assert ctrl.load(f.manifest())
        ctrl.drain_events()

        f.set_time(0)
        ctrl.play()
        assert ctrl.state == ControllerState.PLAYING
        assert ctrl.epoch == 1

        evts = ctrl.drain_events()
        assert has_state_event(evts, ControllerState.PLAYING)

        # Tick produces frames
        f.set_time(0.5)
        ctrl.tick_once()
        pf = ctrl.drain_program_frames()
        assert len(pf) == 1
        assert pf[0].frame_index == 0
        assert pf[0].t_rel == pytest.approx(0.5)
        assert len(pf[0].strips) == 2

    def test_play_from_idle_is_noop(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        ctrl.play()
        assert ctrl.state == ControllerState.IDLE
        assert ctrl.drain_events() == []

    def test_play_while_playing_is_noop(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        assert ctrl.load(f.manifest())

        f.set_time(0)
        ctrl.play()
        ctrl.drain_events()

        ctrl.play()
        assert ctrl.state == ControllerState.PLAYING
        assert ctrl.drain_events() == []


# =========================================================================
# 3. Pause / resume
# =========================================================================


class TestPauseResume:
    def test_pause_and_resume(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        assert ctrl.load(f.manifest())

        f.set_time(0)
        ctrl.play()
        ctrl.drain_events()

        # Tick into program
        f.set_time(1.0)
        ctrl.tick_once()
        ctrl.drain_program_frames()

        # Pause
        ctrl.pause()
        assert ctrl.state == ControllerState.PAUSED
        assert ctrl.current_t_rel == pytest.approx(1.0)
        assert has_state_event(ctrl.drain_events(), ControllerState.PAUSED)

        # No frames while paused
        f.set_time(2.0)
        ctrl.tick_once()
        assert ctrl.drain_program_frames() == []

        # Resume
        ctrl.play()
        assert ctrl.state == ControllerState.PLAYING
        assert has_state_event(ctrl.drain_events(), ControllerState.PLAYING)

        # Tick after resume — should continue from paused position
        f.set_time(2.0)
        ctrl.tick_once()
        pf = ctrl.drain_program_frames()
        assert len(pf) >= 1
        assert pf[0].t_rel >= 0.9


# =========================================================================
# 4. Debug seek
# =========================================================================


class TestDebugSeek:
    def test_from_playing_stays_playing(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        assert ctrl.load(f.manifest())

        f.set_time(0)
        ctrl.play()
        epoch_before = ctrl.epoch
        ctrl.drain_events()

        f.set_time(0.5)
        ctrl.tick_once()
        ctrl.drain_program_frames()

        ctrl.debug_seek(2.0)
        assert ctrl.state == ControllerState.PLAYING
        assert ctrl.epoch == epoch_before + 1
        # gen should NOT be incremented
        assert ctrl.gen == 1  # from load only

        ctrl.tick_once()
        pf = ctrl.drain_program_frames()
        assert len(pf) >= 1
        assert pf[0].t_rel == pytest.approx(2.0)

    def test_from_loaded_goes_to_paused(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        assert ctrl.load(f.manifest())
        ctrl.drain_events()

        ctrl.debug_seek(2.0)
        assert ctrl.state == ControllerState.PAUSED

        ctrl.tick_once()
        pf = ctrl.drain_program_frames()
        assert len(pf) == 1
        assert pf[0].t_rel == pytest.approx(2.0)

    def test_from_paused_stays_paused(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        assert ctrl.load(f.manifest())

        ctrl.debug_seek(1.0)
        epoch1 = ctrl.epoch

        ctrl.debug_seek(3.0)
        assert ctrl.state == ControllerState.PAUSED
        assert ctrl.epoch == epoch1 + 1

        ctrl.tick_once()
        pf = ctrl.drain_program_frames()
        assert any(p.t_rel == pytest.approx(3.0) for p in pf)

    def test_clamps_to_duration(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        assert ctrl.load(f.manifest())

        ctrl.debug_seek(999.0)
        assert ctrl.state == ControllerState.PAUSED
        assert ctrl.current_t_rel <= 5.0
        assert ctrl.current_t_rel == pytest.approx(5.0)

        ctrl.debug_seek(-10.0)
        assert ctrl.current_t_rel == pytest.approx(0.0)

    def test_on_non_debug_device_errors(self):
        dev = FakeDevice()
        ctrl = Controller(
            [StripConfig("strip", 5, dev)],
            clock=lambda: 0,
        )

        m = CompiledManifest(
            duration=5.0,
            strips=[CompiledStripArtifact("strip", 5, b'\x00')],
            safe_intervals=[],
        )
        assert ctrl.load(m)
        ctrl.drain_events()

        ctrl.play()
        epoch_before = ctrl.epoch
        ctrl.drain_events()

        ctrl.debug_seek(2.0)
        assert ctrl.state == ControllerState.PLAYING
        assert ctrl.epoch == epoch_before  # not advanced

        evts = ctrl.drain_events()
        assert len(evts) == 1
        assert evts[0].kind == ControllerEvent.Kind.ERROR

    def test_targeted_debug_seek_ignores_inactive_non_debug_device(self):
        now_ns = [0]
        ctrl = Controller(
            [
                StripConfig("main", 5, MockDevice(duration=5.0)),
                StripConfig("main", 5, MockDevice(duration=5.0)),
                StripConfig("bench", 5, FakeDevice()),
            ],
            clock=lambda: now_ns[0],
        )
        manifest = CompiledManifest(
            duration=5.0,
            strips=[CompiledStripArtifact("main", 5, b'\x00')],
            safe_intervals=[],
        )
        assert ctrl.load(manifest, target_groups=[[0, 1]])
        ctrl.drain_events()

        ctrl.debug_seek(2.0)
        assert ctrl.state == ControllerState.PAUSED
        assert has_state_event(ctrl.drain_events(), ControllerState.PAUSED)

        ctrl.tick_once()
        pf = ctrl.drain_program_frames()
        assert len(pf) == 1
        assert pf[0].t_rel == pytest.approx(2.0)


# =========================================================================
# 5. Production seek
# =========================================================================


class TestProductionSeek:
    def test_inside_safe_interval_hits_target(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        m = f.manifest()
        m.safe_intervals = [(0.0, 0.0), (1.0, 3.0)]
        assert ctrl.load(m)

        ctrl.debug_seek(0.5)
        ctrl.drain_events()

        ctrl.seek(2.0)
        assert ctrl.state == ControllerState.PAUSED

        ctrl.tick_once()
        pf = ctrl.drain_program_frames()
        assert len(pf) >= 1
        assert pf[0].t_rel == pytest.approx(2.0)

    def test_between_intervals_snaps_to_latest(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        m = f.manifest()
        m.safe_intervals = [(0.0, 0.0), (1.0, 2.0), (3.0, 4.0)]
        assert ctrl.load(m)

        ctrl.debug_seek(0.5)
        ctrl.drain_events()

        ctrl.seek(2.5)
        assert ctrl.state == ControllerState.PAUSED

        ctrl.tick_once()
        pf = ctrl.drain_program_frames()
        assert len(pf) >= 1
        assert pf[0].t_rel == pytest.approx(1.0)

    def test_before_first_snaps_to_zero(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        m = f.manifest()
        m.safe_intervals = [(0.0, 0.0), (2.0, 4.0)]
        assert ctrl.load(m)

        ctrl.debug_seek(3.0)
        ctrl.drain_events()

        ctrl.seek(0.5)
        assert ctrl.state == ControllerState.PAUSED

        ctrl.tick_once()
        pf = ctrl.drain_program_frames()
        assert len(pf) >= 1
        assert pf[0].t_rel == pytest.approx(0.0)

    def test_empty_safe_intervals_errors(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        m = f.manifest()
        m.safe_intervals = []
        assert ctrl.load(m)

        ctrl.debug_seek(1.0)
        epoch_before = ctrl.epoch
        state_before = ctrl.state
        ctrl.drain_events()

        ctrl.seek(2.0)
        assert ctrl.state == state_before
        assert ctrl.epoch == epoch_before

        evts = ctrl.drain_events()
        assert len(evts) == 1
        assert evts[0].kind == ControllerEvent.Kind.ERROR

    def test_from_playing_stays_playing(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        m = f.manifest()
        m.safe_intervals = [(0.0, 0.0), (1.0, 4.0)]
        assert ctrl.load(m)

        f.set_time(0)
        ctrl.play()
        epoch_before = ctrl.epoch
        ctrl.drain_events()

        f.set_time(0.5)
        ctrl.tick_once()
        ctrl.drain_program_frames()

        ctrl.seek(2.0)
        assert ctrl.state == ControllerState.PLAYING
        assert ctrl.epoch == epoch_before + 1

        ctrl.tick_once()
        pf = ctrl.drain_program_frames()
        assert len(pf) >= 1
        assert pf[0].t_rel == pytest.approx(2.0)

    def test_from_loaded_goes_to_paused(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        m = f.manifest()
        m.safe_intervals = [(0.0, 0.0), (1.0, 4.0)]
        assert ctrl.load(m)
        ctrl.drain_events()

        ctrl.seek(2.0)
        assert ctrl.state == ControllerState.PAUSED
        assert has_state_event(ctrl.drain_events(), ControllerState.PAUSED)

    def test_from_paused_stays_paused(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        m = f.manifest()
        m.safe_intervals = [(0.0, 0.0), (1.0, 4.0)]
        assert ctrl.load(m)

        ctrl.seek(1.5)
        epoch1 = ctrl.epoch
        ctrl.drain_events()

        ctrl.seek(3.0)
        assert ctrl.state == ControllerState.PAUSED
        assert ctrl.epoch == epoch1 + 1

        ctrl.tick_once()
        pf = ctrl.drain_program_frames()
        assert len(pf) >= 1
        assert pf[0].t_rel == pytest.approx(3.0)

    def test_works_on_non_debug_device(self):
        dev = FakeDevice()
        ctrl = Controller(
            [StripConfig("strip", 5, dev)],
            clock=lambda: 0,
        )

        m = CompiledManifest(
            duration=5.0,
            strips=[CompiledStripArtifact("strip", 5, b'\x00')],
            safe_intervals=[(0.0, 0.0), (1.0, 4.0)],
        )
        assert ctrl.load(m)
        ctrl.drain_events()

        ctrl.seek(2.0)
        assert ctrl.state == ControllerState.PAUSED
        assert has_state_event(ctrl.drain_events(), ControllerState.PAUSED)


# =========================================================================
# 6. Stop
# =========================================================================


class TestStop:
    def test_stop_and_restart(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        assert ctrl.load(f.manifest())

        f.set_time(0)
        ctrl.play()
        ctrl.drain_events()

        f.set_time(1.0)
        ctrl.tick_once()
        ctrl.drain_program_frames()

        ctrl.stop()
        assert ctrl.state == ControllerState.STOPPED
        assert has_state_event(ctrl.drain_events(), ControllerState.STOPPED)

        # Restart
        epoch_before = ctrl.epoch
        ctrl.play()
        assert ctrl.state == ControllerState.PLAYING
        assert ctrl.epoch == epoch_before + 1


# =========================================================================
# 7. End and loop
# =========================================================================


class TestEndAndLoop:
    def test_non_looping_ends(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        assert ctrl.load(f.manifest())

        f.set_time(0)
        ctrl.play()
        ctrl.drain_events()

        f.set_time(5.1)
        ctrl.tick_once()

        assert ctrl.state == ControllerState.ENDED
        assert has_state_event(ctrl.drain_events(), ControllerState.ENDED)

    def test_looping_restarts(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        assert ctrl.load(f.manifest(), loop=True)

        f.set_time(0)
        ctrl.play()
        epoch_before = ctrl.epoch
        ctrl.drain_events()

        f.set_time(5.1)
        ctrl.tick_once()

        assert ctrl.state == ControllerState.PLAYING
        assert ctrl.epoch == epoch_before + 1
        assert has_event(ctrl.drain_events(), ControllerEvent.Kind.LOOPED)

        # Tick after loop — should get frames from restart
        f.set_time(5.6)
        ctrl.tick_once()
        pf = ctrl.drain_program_frames()
        assert len(pf) >= 1


# =========================================================================
# 8. Gen filtering
# =========================================================================


class TestGenFiltering:
    def test_stale_gen_frames_dropped_after_load(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        # First load (gen=1)
        assert ctrl.load(f.manifest())
        f.set_time(0)
        ctrl.play()
        ctrl.drain_events()
        f.set_time(0.5)
        ctrl.tick_once()
        pf1 = ctrl.drain_program_frames()
        assert len(pf1) == 1

        # Second load (gen=2) — don't drain device frames
        assert ctrl.load(f.manifest())
        f.set_time(0)
        ctrl.play()
        ctrl.drain_events()

        f.set_time(0.5)
        ctrl.tick_once()
        pf2 = ctrl.drain_program_frames()
        assert len(pf2) == 1
        assert pf2[0].frame_index == 0


# =========================================================================
# 9. Events
# =========================================================================


class TestEvents:
    def test_full_lifecycle(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        # Load
        assert ctrl.load(f.manifest())
        evts = ctrl.drain_events()
        assert len(evts) == 2
        assert evts[0].kind == ControllerEvent.Kind.SESSION_STARTED
        assert evts[0].session_id == 1
        assert evts[1].kind == ControllerEvent.Kind.STATE_CHANGED
        assert evts[1].state == ControllerState.LOADED

        # Play
        f.set_time(0)
        ctrl.play()
        evts = ctrl.drain_events()
        assert len(evts) == 1
        assert evts[0].kind == ControllerEvent.Kind.STATE_CHANGED
        assert evts[0].state == ControllerState.PLAYING
        assert evts[0].epoch == 1

        # Tick
        f.set_time(0.5)
        ctrl.tick_once()

        # Pause
        ctrl.pause()
        evts = ctrl.drain_events()
        assert len(evts) == 1
        assert evts[0].state == ControllerState.PAUSED

        # Stop
        ctrl.stop()
        evts = ctrl.drain_events()
        assert len(evts) == 1
        assert evts[0].state == ControllerState.STOPPED

        # Error on bad load
        m = CompiledManifest(
            duration=1.0,
            strips=[CompiledStripArtifact("left", 5, b'\xDE')],
            safe_intervals=[],
        )
        assert not ctrl.load(m)
        evts = ctrl.drain_events()
        assert len(evts) == 1
        assert evts[0].kind == ControllerEvent.Kind.ERROR


# =========================================================================
# 10. Safe intervals property
# =========================================================================


class TestSafeIntervals:
    def test_reflects_manifest(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        m = f.manifest()
        m.safe_intervals = [(0.0, 0.0), (1.0, 3.0)]
        assert ctrl.load(m)

        assert ctrl.safe_intervals == [(0.0, 0.0), (1.0, 3.0)]

    def test_cleared_after_failed_load(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        m = f.manifest()
        m.safe_intervals = [(0.0, 0.0), (1.0, 3.0)]
        assert ctrl.load(m)
        assert ctrl.safe_intervals == [(0.0, 0.0), (1.0, 3.0)]

        # Play to enter a non-IDLE state
        f.set_time(0)
        ctrl.play()
        ctrl.drain_events()

        # Failed reload
        f.right.load_should_fail = True
        assert not ctrl.load(f.manifest())

        assert ctrl.safe_intervals == []


# =========================================================================
# Constructor validation
# =========================================================================


class TestConstructor:
    def test_allows_empty_strips(self):
        ctrl = Controller([], clock=lambda: 0)
        assert ctrl.state == ControllerState.IDLE
        assert ctrl.current_t_rel == 0.0
        ctrl.tick_once()
        assert ctrl.drain_events() == []
        assert ctrl.drain_program_frames() == []

    def test_allows_duplicate_strip_ids_in_idle_state(self):
        a = MockDevice()
        b = MockDevice()
        ctrl = Controller(
            [StripConfig("same", 5, a), StripConfig("same", 5, b)],
            clock=lambda: 0,
        )
        assert ctrl.state == ControllerState.IDLE

    def test_load_rejects_duplicate_strip_id_topology(self):
        a = MockDevice()
        b = MockDevice()
        ctrl = Controller(
            [StripConfig("same", 5, a), StripConfig("same", 5, b)],
            clock=lambda: 0,
        )
        manifest = CompiledManifest(
            duration=1.0,
            safe_intervals=[],
            strips=[
                CompiledStripArtifact("same", 5, b"\x00" * 6),
                CompiledStripArtifact("same", 5, b"\x00" * 6),
            ],
        )

        assert ctrl.load(manifest) is False
        events = ctrl.drain_events()
        assert any(
            e.kind == ControllerEvent.Kind.ERROR
            and e.message == "duplicate strip_id requires targeted load support: same"
            for e in events
        )

    def test_rejects_none_device(self):
        with pytest.raises(ValueError, match="no device"):
            Controller(
                [StripConfig("strip", 5, None)],
                clock=lambda: 0,
            )


# =========================================================================
# Debug seek from ENDED
# =========================================================================


class TestSeekFromEnded:
    def test_debug_seek_from_ended(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        assert ctrl.load(f.manifest())

        f.set_time(0)
        ctrl.play()
        ctrl.drain_events()

        f.set_time(5.1)
        ctrl.tick_once()
        assert ctrl.state == ControllerState.ENDED
        ctrl.drain_events()
        ctrl.drain_program_frames()

        ctrl.debug_seek(2.0)
        assert ctrl.state == ControllerState.PAUSED

        ctrl.tick_once()
        pf = ctrl.drain_program_frames()
        assert len(pf) >= 1
        assert pf[0].t_rel == pytest.approx(2.0)

    def test_play_from_ended_restarts(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        assert ctrl.load(f.manifest())

        f.set_time(0)
        ctrl.play()
        ctrl.drain_events()

        f.set_time(5.1)
        ctrl.tick_once()
        assert ctrl.state == ControllerState.ENDED
        ctrl.drain_events()
        ctrl.drain_program_frames()

        ctrl.play()
        assert ctrl.state == ControllerState.PLAYING

        f.set_time(5.6)
        ctrl.tick_once()
        pf = ctrl.drain_program_frames()
        assert len(pf) >= 1
