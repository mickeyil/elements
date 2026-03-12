"""Smoke test: config → compile → NetworkDevice → Controller → ENDED."""

import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

_repo = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo / 'compiler'))

from elemctl.config import Config, DeviceConfig
from elemctl.controller import Controller, ControllerState, StripConfig
from elemctl.device import DeviceState
from elemctl.run import run_controller

NETWORK_SIM_BIN = _repo / 'build' / 'network_sim'
STRIP_LENGTH = 5


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f'rep_{rep.when}', rep)


def _find_free_tcp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _find_free_udp_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()
    return port


class SimProcess:
    def __init__(self, proc, tcp_port, frame_port):
        import threading
        self.proc = proc
        self.tcp_port = tcp_port
        self.frame_port = frame_port
        self._stderr_lines: list[bytes] = []
        self._ready_event = threading.Event()
        self._stdout_reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._stderr_reader = threading.Thread(target=self._read_stderr, daemon=True)
        self._stdout_reader.start()
        self._stderr_reader.start()

    def _read_stdout(self):
        for line in self.proc.stdout:
            if b'Waiting for controller' in line:
                self._ready_event.set()

    def _read_stderr(self):
        for line in self.proc.stderr:
            self._stderr_lines.append(line)

    def wait_ready(self, timeout=5.0):
        result = self._ready_event.wait(timeout=timeout)
        self._ready_event.clear()
        return result

    def dump_stderr(self):
        return b''.join(self._stderr_lines).decode(errors='replace')


@pytest.fixture()
def sim_process(request):
    if not NETWORK_SIM_BIN.exists():
        pytest.skip(f'network_sim not built at {NETWORK_SIM_BIN}')

    tcp_port = _find_free_tcp_port()
    frame_port = _find_free_udp_port()

    proc = subprocess.Popen(
        [
            str(NETWORK_SIM_BIN),
            '--tcp-port', str(tcp_port),
            '--device-uid', 'sim-test',
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    sim = SimProcess(proc, tcp_port, frame_port)
    if not sim.wait_ready():
        proc.kill()
        proc.wait()
        pytest.fail('network_sim did not start listening in time')

    yield sim

    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=3.0)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()

    rep = getattr(request.node, 'rep_call', None)
    if rep and rep.failed:
        stderr = sim.dump_stderr()
        if stderr:
            print(f'\n--- network_sim stderr ---\n{stderr}--- end ---')


class TestRunController:
    def test_smoke_ends_naturally(self, sim_process):
        """Full pipeline: config → compile → wire → play → ENDED."""
        from elements.dsl import _builder, strip, wave, build_manifest, sec

        # Build config pointing at the sim process
        config = Config(
            frame_port=sim_process.frame_port,
            devices=[
                DeviceConfig(
                    device_id=1,
                    device_uid="sim-test",
                    device_type="sim",
                    host="127.0.0.1",
                    tcp_port=sim_process.tcp_port,
                    strip_id="test",
                    length=STRIP_LENGTH,
                ),
            ],
        )

        # Compile a short program
        _builder.reset()
        s = strip('test', length=STRIP_LENGTH, type='RGB')
        px = s.pixels(f'0-{STRIP_LENGTH - 1}')
        w = wave(channel='V', h=0, s=1.0, v=0.0,
                 min_val=0.0, max_val=1.0, period=4, phase0=0, pixel_step=0)
        w.schedule(px, at=0, duration=sec(0.5))
        manifest = build_manifest(beat=0.5, duration=0.5)

        state = run_controller(config, manifest, loop=False)
        assert state == ControllerState.ENDED

    def test_loop_does_not_false_stall(self, sim_process):
        """Looping playback should not trigger stall detection."""
        from elements.dsl import _builder, strip, wave, build_manifest, sec

        config = Config(
            frame_port=sim_process.frame_port,
            devices=[
                DeviceConfig(
                    device_id=1,
                    device_uid="sim-test",
                    device_type="sim",
                    host="127.0.0.1",
                    tcp_port=sim_process.tcp_port,
                    strip_id="test",
                    length=STRIP_LENGTH,
                ),
            ],
        )

        # Very short program so it loops multiple times quickly
        _builder.reset()
        s = strip('test', length=STRIP_LENGTH, type='RGB')
        px = s.pixels(f'0-{STRIP_LENGTH - 1}')
        w = wave(channel='V', h=0, s=1.0, v=0.0,
                 min_val=0.0, max_val=1.0, period=4, phase0=0, pixel_step=0)
        w.schedule(px, at=0, duration=sec(0.3))
        manifest = build_manifest(beat=0.5, duration=0.3)

        # Let it loop for >1 cycle, then stop via stop_event
        stop = threading.Event()

        def _stop_later():
            time.sleep(0.8)  # enough for ~2 loops of 0.3s
            stop.set()

        threading.Thread(target=_stop_later, daemon=True).start()

        state = run_controller(
            config, manifest, loop=True,
            stop_event=stop, stall_timeout=0.5,
        )
        # Should still be PLAYING (stopped by event), not stalled
        assert state == ControllerState.PLAYING


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


class _SilentDevice:
    """Device that accepts commands optimistically but never emits frames.

    Mimics the real NetworkDevice failure mode: start() succeeds locally
    (state → PLAYING, t_rel advances from local clock), but no UDP frames
    ever arrive.
    """

    def __init__(self, device_id, host, tcp_port, device_type, strip_length, frame_port, udp_receiver):
        self._state = DeviceState.IDLE
        self._t0_ns = 0

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

    def close(self):
        self._state = DeviceState.IDLE


class TestStallDetection:
    """Test that stalled playback aborts instead of hanging."""

    def _make_config(self):
        return Config(
            frame_port=1,  # unused by _NoopReceiver
            devices=[
                DeviceConfig(
                    device_id=1,
                    device_uid="fake",
                    device_type="sim",
                    host="127.0.0.1",
                    tcp_port=1,
                    strip_id="test",
                    length=5,
                ),
            ],
        )

    def _make_manifest(self, duration=10.0):
        from elements.types import CompiledManifest, CompiledStripArtifact
        return CompiledManifest(
            duration=duration,
            strips=[CompiledStripArtifact(strip_id="test", length=5, blob=b'\x00')],
            safe_intervals=[],
        )

    def test_stall_aborts(self):
        """Device goes PLAYING but emits no frames → runner aborts."""
        state = run_controller(
            self._make_config(),
            self._make_manifest(duration=10.0),
            stall_timeout=0.15,
            receiver_factory=_NoopReceiver,
            device_factory=_SilentDevice,
        )
        # Runner should abort (stall), not reach ENDED
        assert state == ControllerState.PLAYING

    def test_stall_aborts_quickly(self):
        """Stall abort should happen within ~stall_timeout, not hang."""
        t0 = time.monotonic()
        run_controller(
            self._make_config(),
            self._make_manifest(duration=10.0),
            stall_timeout=0.2,
            receiver_factory=_NoopReceiver,
            device_factory=_SilentDevice,
        )
        elapsed = time.monotonic() - t0
        assert elapsed < 1.0, f"took {elapsed:.1f}s, should abort in ~0.2s"
