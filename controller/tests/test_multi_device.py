"""Multi-device integration test: 2 network_sim processes + Controller.

Launches 2 network_sim subprocesses (one per strip), connects via
NetworkDevice, runs through the full Controller pipeline, and verifies
that ProgramFrames assemble correctly with real TCP/UDP transport.
"""

import sys
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

_repo = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo / 'compiler'))

from elements.dsl import strip, spark, build_manifest, sec, _builder

from elemctl.controller import (
    Controller, ControllerEvent, ControllerState, ProgramFrame, StripConfig,
)
from elemctl.network_device import NetworkDevice
from elemctl.udp_receiver import UdpFrameReceiver

from .sim_helpers import find_free_tcp_port, find_free_udp_port, start_sim, stop_sim

STRIP_LENGTH = 10
FADE = 1.0
PROGRAM_DURATION = 1.0


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------

def _make_symmetric_manifest():
    """Both strips get the same white spark at t=0."""
    _builder.reset()
    s1 = strip('strip_a', length=STRIP_LENGTH)
    s2 = strip('strip_b', length=STRIP_LENGTH)
    sp = spark(color='white', fade=FADE)
    sp.schedule(s1.pixels('0-9'), at=0, duration=sec(PROGRAM_DURATION))
    sp.schedule(s2.pixels('0-9'), at=0, duration=sec(PROGRAM_DURATION))
    return build_manifest(beat=1.0, duration=PROGRAM_DURATION)


def _make_offset_manifest():
    """Strip B's spark starts 0.2s later than strip A's."""
    OFFSET = 0.2
    _builder.reset()
    s1 = strip('strip_a', length=STRIP_LENGTH)
    s2 = strip('strip_b', length=STRIP_LENGTH)
    sp = spark(color='white', fade=FADE)
    sp.schedule(s1.pixels('0-9'), at=0, duration=sec(PROGRAM_DURATION))
    sp.schedule(s2.pixels('0-9'), at=sec(OFFSET),
                duration=sec(PROGRAM_DURATION - OFFSET))
    return build_manifest(beat=1.0, duration=PROGRAM_DURATION)


# ---------------------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------------------

def _strip_mean(rgb_bytes: bytes) -> float:
    """Mean byte value across all channels in a strip."""
    return sum(rgb_bytes) / len(rgb_bytes)


def _strip_is_uniform(rgb_bytes: bytes) -> bool:
    """True if all pixels have the same R=G=B value (white-on-black)."""
    if len(rgb_bytes) < 3:
        return True
    r0 = rgb_bytes[0]
    return all(rgb_bytes[i] == r0 for i in range(len(rgb_bytes)))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def two_sims(request):
    """Start 2 network_sim subprocesses, yield (sim1, sim2, frame_port)."""
    tcp_port_1 = find_free_tcp_port()
    tcp_port_2 = find_free_tcp_port()
    frame_port = find_free_udp_port()

    sim1 = start_sim(tcp_port_1, frame_port, STRIP_LENGTH, device_id=1)
    try:
        sim2 = start_sim(tcp_port_2, frame_port, STRIP_LENGTH, device_id=2)
    except Exception:
        stop_sim(sim1)
        raise

    yield sim1, sim2, frame_port

    stop_sim(sim1)
    stop_sim(sim2)

    # Surface stderr on failure
    rep = getattr(request.node, 'rep_call', None)
    if rep and rep.failed:
        for label, sim in [('sim1', sim1), ('sim2', sim2)]:
            stderr = sim.dump_stderr()
            if stderr:
                print(f'\n--- {label} stderr ---\n{stderr}--- end ---')


# ---------------------------------------------------------------------------
# Shared playback runner
# ---------------------------------------------------------------------------

@dataclass
class PlaybackResult:
    frames: list[ProgramFrame]
    ended: bool
    errors: list[ControllerEvent]


def _run_playback(two_sims, manifest, min_frames=5, timeout=5.0):
    """Load + play + poll until ENDED or timeout. Returns PlaybackResult."""
    sim1, sim2, frame_port = two_sims

    receiver = UdpFrameReceiver(frame_port)
    dev1 = NetworkDevice(
        device_id=1, host='127.0.0.1', tcp_port=sim1.tcp_port,
        device_type='sim', udp_receiver=receiver,
    )
    dev2 = NetworkDevice(
        device_id=2, host='127.0.0.1', tcp_port=sim2.tcp_port,
        device_type='sim', udp_receiver=receiver,
    )

    try:
        strips = [
            StripConfig('strip_a', STRIP_LENGTH, dev1),
            StripConfig('strip_b', STRIP_LENGTH, dev2),
        ]
        ctrl = Controller(strips)

        ok = ctrl.load(manifest)
        assert ok, f'load failed: {ctrl.drain_events()}'
        ctrl.drain_events()

        ctrl.play()
        ctrl.drain_events()

        frames: list[ProgramFrame] = []
        errors: list[ControllerEvent] = []
        ended = False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            receiver.poll()
            ctrl.tick_once()
            frames.extend(ctrl.drain_program_frames())
            for evt in ctrl.drain_events():
                if evt.kind == ControllerEvent.Kind.ERROR:
                    errors.append(evt)
                if (evt.kind == ControllerEvent.Kind.STATE_CHANGED
                        and evt.state == ControllerState.ENDED):
                    ended = True
            if ended and len(frames) >= min_frames:
                break
            time.sleep(0.025)

        return PlaybackResult(frames=frames, ended=ended, errors=errors)
    finally:
        dev1.close()
        dev2.close()
        receiver.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMultiDevice:
    def test_symmetric_spark(self, two_sims):
        """Both strips see the same spark — frames assemble with identical RGB."""
        manifest = _make_symmetric_manifest()
        result = _run_playback(two_sims, manifest, min_frames=3)

        assert not result.errors, f'unexpected errors: {result.errors}'
        assert result.ended, 'controller did not reach ENDED'
        assert len(result.frames) >= 3, \
            f'expected >= 3 ProgramFrames, got {len(result.frames)}'

        for pf in result.frames:
            assert len(pf.strips) == 2, f'expected 2 strips, got {len(pf.strips)}'
            assert len(pf.strips[0]) == STRIP_LENGTH * 3
            assert len(pf.strips[1]) == STRIP_LENGTH * 3
            # Both strips have the same animation — RGB should match within ±1
            # (devices compute t_rel independently from wall-clock, so tiny
            # timing skew can cause ±1 rounding difference)
            for j in range(len(pf.strips[0])):
                diff = abs(pf.strips[0][j] - pf.strips[1][j])
                assert diff <= 1, \
                    f'byte {j} differs by {diff} at frame_index={pf.frame_index}, ' \
                    f't_rel={pf.t_rel:.3f}: {pf.strips[0][j]} vs {pf.strips[1][j]}'
            # White spark: all pixels uniform (R=G=B)
            assert _strip_is_uniform(pf.strips[0]), \
                f'strip_a not uniform at frame_index={pf.frame_index}'

        # Brightness must decrease over time (spark fades out)
        means = [_strip_mean(pf.strips[0]) for pf in result.frames]
        for i in range(1, len(means)):
            assert means[i] <= means[i - 1] + 1, \
                f'brightness increased: {means[i - 1]:.1f} -> {means[i]:.1f}'

    def test_offset_spark_strip_identity(self, two_sims):
        """Strip B starts 0.2s later — strip_a is always dimmer than strip_b.

        Both strips run a white spark fade-out. Strip A starts at t=0,
        strip B starts at t=0.2s. So at any assembled frame, strip A is
        further into its fade and therefore dimmer. This verifies strip
        routing without depending on pf.t_rel as cross-device truth.
        """
        manifest = _make_offset_manifest()
        result = _run_playback(two_sims, manifest, min_frames=25)

        assert not result.errors, f'unexpected errors: {result.errors}'
        assert result.ended, 'controller did not reach ENDED'
        assert len(result.frames) >= 3, \
            f'expected >= 3 ProgramFrames, got {len(result.frames)}'

        # Find frames where both strips are non-black (both sparks active).
        # Strip B starts later, so early frames may have strip_b still dark.
        both_lit = [
            pf for pf in result.frames
            if _strip_mean(pf.strips[0]) > 5 and _strip_mean(pf.strips[1]) > 5
        ]
        assert len(both_lit) >= 1, \
            f'no frames with both strips lit; means: ' \
            f'{[(_strip_mean(pf.strips[0]), _strip_mean(pf.strips[1])) for pf in result.frames[:5]]}'

        # Strip A started first → further into fade → dimmer than strip B
        for pf in both_lit:
            mean_a = _strip_mean(pf.strips[0])
            mean_b = _strip_mean(pf.strips[1])
            assert mean_a < mean_b, \
                f'strip_a ({mean_a:.1f}) should be dimmer than strip_b ({mean_b:.1f}) ' \
                f'at frame_index={pf.frame_index}'

    def test_frames_have_monotonic_t_rel(self, two_sims):
        """ProgramFrame t_rel values are strictly increasing."""
        manifest = _make_symmetric_manifest()
        result = _run_playback(two_sims, manifest, min_frames=3)

        assert not result.errors, f'unexpected errors: {result.errors}'
        assert result.ended, 'controller did not reach ENDED'
        assert len(result.frames) >= 3, \
            f'expected >= 3 ProgramFrames, got {len(result.frames)}'

        t_rels = [pf.t_rel for pf in result.frames]
        for i in range(1, len(t_rels)):
            assert t_rels[i] > t_rels[i - 1], \
                f't_rel not monotonic: {t_rels[i - 1]:.4f} >= {t_rels[i]:.4f}'
