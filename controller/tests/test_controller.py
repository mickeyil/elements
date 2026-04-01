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

    def __init__(
        self,
        duration: float = 5.0,
        strip_length: int = 5,
        *,
        produces_program_frames: bool = True,
        frame_byte: int = 0x00,
    ):
        self._state = DeviceState.IDLE
        self._gen = 0
        self._frame_index = 0
        self._t0_ns = 0
        self._duration_sec = duration
        self._strip_length = strip_length
        self._produces_program_frames = produces_program_frames
        self._frame_byte = frame_byte & 0xFF
        self._last_t_rel = 0.0
        self._frames: list[DeviceFrame] = []
        self.load_should_fail = False
        self.is_connected = True
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
                rgb=bytes([self._frame_byte]) * (self._strip_length * 3),
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
            rgb=bytes([self._frame_byte]) * (self._strip_length * 3),
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

    def produces_program_frames(self) -> bool:
        return self._produces_program_frames

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
            rgb=bytes([self._frame_byte]) * (self._strip_length * 3),
        ))
        self._frame_index = 1
        if self._state == DeviceState.PLAYING:
            self._t0_ns = now_ns - int(t_rel * 1e9)
        else:
            self._state = DeviceState.PAUSED


class MisreportingCurrentTimeDevice(MockDevice):
    """Mock device whose current_t_rel() lies to the controller."""

    def __init__(self, reported_t_rel: float, **kwargs):
        super().__init__(**kwargs)
        self._reported_t_rel = reported_t_rel

    def current_t_rel(self, now_ns: int) -> float:
        return self._reported_t_rel


# ---------------------------------------------------------------------------
# FakeDevice — device without debug_seek support
# ---------------------------------------------------------------------------


class FakeDevice:
    """Device without debug_seek support (simulates real hardware)."""

    def __init__(self):
        self._state = DeviceState.IDLE
        self._gen = 0
        self._last_t_rel = 0.0
        self.is_connected = True

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

    def produces_program_frames(self) -> bool:
        return False

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
        self.sim = MockDevice(duration=5.0, produces_program_frames=True)
        self.esp = MockDevice(duration=5.0, produces_program_frames=False)
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
        self.left_sim = MockDevice(duration=5.0, produces_program_frames=True)
        self.left_esp = MockDevice(duration=5.0, produces_program_frames=False)
        self.right_sim = MockDevice(duration=5.0, produces_program_frames=True)
        self.right_esp = MockDevice(duration=5.0, produces_program_frames=False)
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
        assert ctrl.session_target_groups == [[0], [1]]
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
        assert ctrl.session_target_groups == [[0, 1]]

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

    def test_current_t_rel_uses_controller_clock_not_device_reports(self):
        now_ns = [0]
        ctrl = Controller(
            [
                StripConfig(
                    "left",
                    5,
                    MisreportingCurrentTimeDevice(reported_t_rel=111.0, duration=5.0),
                ),
                StripConfig(
                    "right",
                    5,
                    MisreportingCurrentTimeDevice(reported_t_rel=222.0, duration=5.0),
                ),
            ],
            clock=lambda: now_ns[0],
        )
        manifest = CompiledManifest(
            duration=5.0,
            strips=[
                CompiledStripArtifact("left", 5, b'\x00'),
                CompiledStripArtifact("right", 5, b'\x00'),
            ],
            safe_intervals=[],
        )
        assert ctrl.load(manifest)

        now_ns[0] = _sec(0.0)
        ctrl.play()

        now_ns[0] = _sec(0.75)
        assert ctrl.current_t_rel == pytest.approx(0.75)

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

    def test_pause_uses_controller_clock_not_device_reports(self):
        now_ns = [0]
        ctrl = Controller(
            [
                StripConfig(
                    "left",
                    5,
                    MisreportingCurrentTimeDevice(reported_t_rel=111.0, duration=5.0),
                ),
                StripConfig(
                    "right",
                    5,
                    MisreportingCurrentTimeDevice(reported_t_rel=222.0, duration=5.0),
                ),
            ],
            clock=lambda: now_ns[0],
        )
        manifest = CompiledManifest(
            duration=5.0,
            strips=[
                CompiledStripArtifact("left", 5, b'\x00'),
                CompiledStripArtifact("right", 5, b'\x00'),
            ],
            safe_intervals=[],
        )
        assert ctrl.load(manifest)

        now_ns[0] = _sec(0.0)
        ctrl.play()

        now_ns[0] = _sec(1.0)
        ctrl.pause()
        assert ctrl.state == ControllerState.PAUSED
        assert ctrl.current_t_rel == pytest.approx(1.0)


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
        assert ctrl.current_t_rel == pytest.approx(2.0)
        # gen should NOT be incremented
        assert ctrl.gen == 1  # from load only

        f.set_time(1.0)
        assert ctrl.current_t_rel == pytest.approx(2.5)
        ctrl.tick_once()
        pf = ctrl.drain_program_frames()
        assert len(pf) >= 1
        assert any(p.t_rel == pytest.approx(2.5) for p in pf)

    def test_from_loaded_goes_to_paused(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        assert ctrl.load(f.manifest())
        ctrl.drain_events()

        ctrl.debug_seek(2.0)
        assert ctrl.state == ControllerState.PAUSED
        assert ctrl.current_t_rel == pytest.approx(2.0)

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

    def test_stop_preserves_already_assembled_program_frames(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        assert ctrl.load(f.manifest())

        f.set_time(0)
        ctrl.play()
        ctrl.drain_events()

        f.set_time(1.0)
        ctrl.tick_once()

        ctrl.stop()
        assert ctrl.state == ControllerState.STOPPED

        frames = ctrl.drain_program_frames()
        assert len(frames) == 1


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

    def test_non_looping_ends_from_controller_clock_not_device_reports(self):
        now_ns = [0]
        ctrl = Controller(
            [
                StripConfig(
                    "left",
                    5,
                    MisreportingCurrentTimeDevice(reported_t_rel=0.0, duration=5.0),
                ),
                StripConfig(
                    "right",
                    5,
                    MisreportingCurrentTimeDevice(reported_t_rel=0.0, duration=5.0),
                ),
            ],
            clock=lambda: now_ns[0],
        )
        manifest = CompiledManifest(
            duration=5.0,
            strips=[
                CompiledStripArtifact("left", 5, b'\x00'),
                CompiledStripArtifact("right", 5, b'\x00'),
            ],
            safe_intervals=[],
        )
        assert ctrl.load(manifest)

        now_ns[0] = _sec(0.0)
        ctrl.play()
        ctrl.drain_events()

        now_ns[0] = _sec(5.1)
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
# 9. Abort and frame-stream capability
# =========================================================================


class TestAbortAndFrameStream:
    def test_program_frame_stream_enabled_for_all_frame_devices(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)

        assert ctrl.load(f.manifest())
        assert ctrl.program_frame_stream_enabled is True

    def test_program_frame_stream_enabled_for_mixed_mirror(self):
        now_ns = [0]
        esp = MockDevice(produces_program_frames=False, frame_byte=0x22)
        sim = MockDevice(produces_program_frames=True, frame_byte=0x11)
        ctrl = Controller(
            [
                StripConfig("main", 5, esp),
                StripConfig("main", 5, sim),
            ],
            clock=lambda: now_ns[0],
        )
        manifest = CompiledManifest(
            duration=5.0,
            strips=[CompiledStripArtifact("main", 5, b'\x00')],
            safe_intervals=[],
        )

        assert ctrl.load(manifest, target_groups=[[0, 1]])
        assert ctrl.program_frame_stream_enabled is True

        now_ns[0] = _sec(0.0)
        ctrl.play()
        ctrl.drain_events()

        now_ns[0] = _sec(1.0)
        ctrl.tick_once()

        frames = ctrl.drain_program_frames()
        assert len(frames) == 1
        assert frames[0].strips == [bytes([0x11]) * 15]

    def test_detach_keeps_session_membership_but_suspends_unserved_stream(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        assert ctrl.load(f.manifest())
        assert ctrl.program_frame_stream_enabled is True

        assert ctrl.detach_device(f.left) is True
        assert ctrl.uses_device(f.left) is True
        assert ctrl.program_frame_stream_enabled is False

        assert ctrl.mark_transport_attached(f.left) is True
        assert ctrl.program_frame_stream_enabled is False

    def test_detaching_frame_producing_mirror_suspends_observer_stream(self):
        f = MirrorFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        assert ctrl.load(f.manifest(), target_groups=[[0, 1]])
        assert ctrl.program_frame_stream_enabled is True

        assert ctrl.detach_device(f.sim) is True
        assert ctrl.program_frame_stream_enabled is False

    def test_detaching_non_frame_mirror_keeps_slot_served(self):
        f = MirrorFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        assert ctrl.load(f.manifest(), target_groups=[[0, 1]])
        assert ctrl.program_frame_stream_enabled is True

        assert ctrl.detach_device(f.esp) is True
        assert ctrl.program_frame_stream_enabled is True

        f.set_time(0.0)
        ctrl.play()
        ctrl.drain_events()

        f.set_time(0.5)
        ctrl.tick_once()
        frames = ctrl.drain_program_frames()
        assert len(frames) == 1
        assert frames[0].t_rel == pytest.approx(0.5)

    def test_abort_from_playing_resets_runtime_without_resetting_identity(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        assert ctrl.load(f.manifest())

        f.set_time(0)
        ctrl.play()
        ctrl.drain_events()
        f.set_time(1.0)
        ctrl.tick_once()
        ctrl.drain_program_frames()

        session_id = ctrl.session_id
        gen = ctrl.gen
        f.right.is_connected = False

        ctrl.abort("device lost transport")

        assert ctrl.state == ControllerState.IDLE
        assert ctrl.session_id == session_id
        assert ctrl.gen == gen
        assert ctrl.duration == 0.0
        assert ctrl.session_strips == []
        assert ctrl.session_target_groups == []
        assert ctrl.program_frame_stream_enabled is False
        assert f.left.stop_calls == 1
        assert f.right.stop_calls == 0
        assert ctrl.drain_program_frames() == []

        evts = ctrl.drain_events()
        assert [evt.kind for evt in evts] == [
            ControllerEvent.Kind.ERROR,
            ControllerEvent.Kind.STATE_CHANGED,
        ]
        assert evts[0].message == "device lost transport"
        assert evts[1].state == ControllerState.IDLE

    def test_abort_on_idle_is_noop(self):
        ctrl = Controller([], clock=lambda: 0)
        ctrl.abort("noop")
        assert ctrl.state == ControllerState.IDLE
        assert ctrl.drain_events() == []

    def test_tick_once_drains_and_discards_frames_when_any_logical_strip_lacks_frame_source(self):
        now_ns = [0]
        left_sim = MockDevice(produces_program_frames=True)
        left_esp = MockDevice(produces_program_frames=False)
        right_esp = MockDevice(produces_program_frames=False)
        ctrl = Controller(
            [
                StripConfig("left", 5, left_sim),
                StripConfig("left", 5, left_esp),
                StripConfig("right", 5, right_esp),
            ],
            clock=lambda: now_ns[0],
        )
        manifest = CompiledManifest(
            duration=5.0,
            strips=[
                CompiledStripArtifact("left", 5, b'\x00'),
                CompiledStripArtifact("right", 5, b'\x00'),
            ],
            safe_intervals=[],
        )

        assert ctrl.load(manifest, target_groups=[[0, 1], [2]])
        assert ctrl.program_frame_stream_enabled is False
        now_ns[0] = _sec(0.0)
        ctrl.play()
        ctrl.drain_events()

        now_ns[0] = _sec(1.0)
        ctrl.tick_once()

        assert ctrl.drain_program_frames() == []
        assert left_sim.drain_frames() == []
        assert left_esp.drain_frames() == []
        assert right_esp.drain_frames() == []

    def test_zero_serving_participants_can_still_end(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        assert ctrl.load(f.manifest())

        f.set_time(0.0)
        ctrl.play()
        ctrl.drain_events()

        assert ctrl.detach_device(f.left) is True
        assert ctrl.detach_device(f.right) is True
        assert ctrl.program_frame_stream_enabled is False

        f.set_time(5.1)
        ctrl.tick_once()

        assert ctrl.state == ControllerState.ENDED
        assert has_state_event(ctrl.drain_events(), ControllerState.ENDED)

    def test_zero_serving_participants_can_still_loop(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        assert ctrl.load(f.manifest(), loop=True)

        f.set_time(0.0)
        ctrl.play()
        epoch_before = ctrl.epoch
        ctrl.drain_events()

        assert ctrl.detach_device(f.left) is True
        assert ctrl.detach_device(f.right) is True

        f.set_time(5.1)
        ctrl.tick_once()

        assert ctrl.state == ControllerState.PLAYING
        assert ctrl.epoch == epoch_before + 1
        assert has_event(ctrl.drain_events(), ControllerEvent.Kind.LOOPED)

    def test_tick_once_drains_and_discards_inactive_device_frames(self):
        now_ns = [0]
        active = MockDevice(produces_program_frames=True)
        inactive = MockDevice(produces_program_frames=True)
        ctrl = Controller(
            [
                StripConfig("left", 5, active),
                StripConfig("right", 5, inactive),
            ],
            clock=lambda: now_ns[0],
        )
        manifest = CompiledManifest(
            duration=5.0,
            strips=[CompiledStripArtifact("left", 5, b'\x00')],
            safe_intervals=[],
        )

        assert ctrl.load(manifest, target_groups=[[0]])
        now_ns[0] = _sec(0.0)
        ctrl.play()
        ctrl.drain_events()

        inactive._frames.append(DeviceFrame(
            gen=99,
            frame_index=123,
            t_rel=0.25,
            rgb=b'\xAA' * 15,
        ))

        now_ns[0] = _sec(1.0)
        ctrl.tick_once()

        frames = ctrl.drain_program_frames()
        assert len(frames) == 1
        assert frames[0].frame_index == 0
        assert inactive.drain_frames() == []

    def test_uses_device_matches_active_runtime_membership(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        assert ctrl.load(f.manifest())

        assert ctrl.uses_device(f.left) is True
        assert ctrl.uses_device(f.right) is True

        other = MockDevice()
        assert ctrl.uses_device(other) is False


class TestLiveResume:
    def test_session_artifact_for_returns_blob_and_manifest_length(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        manifest = CompiledManifest(
            duration=5.0,
            strips=[
                CompiledStripArtifact("left", 3, b"\x11\x22"),
                CompiledStripArtifact("right", 4, b"\x33\x44"),
            ],
            safe_intervals=[],
        )
        assert ctrl.load(manifest)

        assert ctrl.session_artifact_for(f.left) == (b"\x11\x22", 3)
        assert ctrl.session_artifact_for(f.right) == (b"\x33\x44", 4)

    def test_live_resume_keeps_session_artifacts_and_uses_current_safe_time(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        manifest = CompiledManifest(
            duration=5.0,
            strips=[
                CompiledStripArtifact("left", 5, b"\x11"),
                CompiledStripArtifact("right", 5, b"\x22"),
            ],
            safe_intervals=[(1.0, 1.5), (3.0, 3.5)],
        )
        assert ctrl.load(manifest)
        assert ctrl._session_artifacts == [b"\x11", b"\x22"]

        f.set_time(0.0)
        ctrl.play()
        ctrl.drain_events()

        assert ctrl.detach_device(f.right) is True
        assert ctrl.mark_transport_attached(f.right) is True
        assert ctrl.is_device_attached(f.right) is True
        assert ctrl.is_device_serving(f.right) is False

        f.set_time(1.25)
        assert ctrl.live_resume_device(f.right, f.clock()) is True

        assert ctrl.is_device_serving(f.right) is True
        assert f.right.load_calls == 2
        assert f.right.jump_calls == 1
        assert f.right.resume_calls == 1
        assert f.right.current_t_rel(f.clock()) == pytest.approx(1.25)

    def test_live_resume_uses_next_safe_interval_start(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        manifest = CompiledManifest(
            duration=5.0,
            strips=[
                CompiledStripArtifact("left", 5, b"\x00"),
                CompiledStripArtifact("right", 5, b"\x00"),
            ],
            safe_intervals=[(1.0, 1.5), (3.0, 3.5)],
        )
        assert ctrl.load(manifest)

        f.set_time(0.0)
        ctrl.play()
        ctrl.drain_events()

        assert ctrl.detach_device(f.right) is True
        assert ctrl.mark_transport_attached(f.right) is True

        f.set_time(2.0)
        assert ctrl.live_resume_device(f.right, f.clock()) is True
        assert f.right.current_t_rel(f.clock()) == pytest.approx(3.0)

    def test_live_resume_past_last_safe_interval_leaves_device_not_serving(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        manifest = CompiledManifest(
            duration=5.0,
            strips=[
                CompiledStripArtifact("left", 5, b"\x00"),
                CompiledStripArtifact("right", 5, b"\x00"),
            ],
            safe_intervals=[(1.0, 1.5)],
        )
        assert ctrl.load(manifest)

        f.set_time(0.0)
        ctrl.play()
        ctrl.drain_events()

        assert ctrl.detach_device(f.right) is True
        assert ctrl.mark_transport_attached(f.right) is True

        f.set_time(2.0)
        assert ctrl.live_resume_device(f.right, f.clock()) is False
        assert ctrl.is_device_serving(f.right) is False
        assert f.right.load_calls == 1

    def test_live_resume_from_paused_loads_and_jumps_without_resume(self):
        f = DualFixture()
        ctrl = Controller(f.strips(), clock=f.clock)
        assert ctrl.load(f.manifest())

        f.set_time(0.0)
        ctrl.play()
        ctrl.drain_events()
        f.set_time(1.2)
        ctrl.pause()
        ctrl.drain_events()

        assert ctrl.detach_device(f.right) is True
        assert ctrl.mark_transport_attached(f.right) is True

        assert ctrl.live_resume_device(f.right, f.clock()) is True
        assert ctrl.is_device_serving(f.right) is True
        assert f.right.load_calls == 2
        assert f.right.jump_calls == 1
        assert f.right.resume_calls == 0
        assert f.right.state() == DeviceState.PAUSED
        assert f.right.current_t_rel(f.clock()) == pytest.approx(ctrl.current_t_rel)


# =========================================================================
# 10. Events
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
# 11. Safe intervals property
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

    def test_rejects_reused_device_object(self):
        shared = MockDevice()
        with pytest.raises(ValueError, match="one strip per device"):
            Controller(
                [
                    StripConfig("left", 5, shared),
                    StripConfig("right", 5, shared),
                ],
                clock=lambda: 0,
            )


class TestGenWrap:
    def test_peek_and_advance_wrap_skip_zero(self):
        ctrl = Controller([], clock=lambda: 0)

        ctrl._gen = 0xFFFE
        assert ctrl._peek_next_gen() == 0xFFFF
        assert ctrl._advance_gen() == 0xFFFF
        assert ctrl._peek_next_gen() == 1
        assert ctrl._advance_gen() == 1

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
