"""End-to-end integration test: network_sim subprocess + NetworkDevice.

Starts a network_sim C++ process, connects via NetworkDevice, loads a
compiled blob, starts playback, and verifies that UDP frames arrive.
"""

import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

# Ensure compiler package is importable
_repo = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo / 'compiler'))

from elemctl.device import DeviceFrame, DeviceState
from elemctl.network_device import NetworkDevice
from elemctl.udp_receiver import UdpFrameReceiver

NETWORK_SIM_BIN = _repo / 'build' / 'network_sim'
STRIP_LENGTH = 5


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Stash test outcome on the item node so fixtures can inspect it."""
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f'rep_{rep.when}', rep)


def _find_free_tcp_port() -> int:
    """Bind a TCP socket to port 0, read back the assigned port, close."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _find_free_udp_port() -> int:
    """Bind a UDP socket to port 0, read back the assigned port, close."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(('', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _make_blob() -> bytes:
    """Compile a minimal test program using the DSL."""
    from elements.dsl import strip, wave, build, sec

    s = strip('test', length=STRIP_LENGTH, type='RGB')
    px = s.pixels(f'0-{STRIP_LENGTH - 1}')
    w = wave(channel='V', h=0, s=1.0, v=0.0,
             min_val=0.0, max_val=1.0, period=4, phase0=0, pixel_step=0)
    w.schedule(px, at=0, duration=sec(2.0))
    blobs = build(beat=0.5, duration=2.0)
    return blobs['test']


class SimProcess:
    """Manages a network_sim subprocess with stdout-based readiness detection."""

    def __init__(self, proc: subprocess.Popen, tcp_port: int, frame_port: int):
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

    def _read_stdout(self) -> None:
        for line in self.proc.stdout:
            if b'Waiting for controller' in line:
                self._ready_event.set()

    def _read_stderr(self) -> None:
        for line in self.proc.stderr:
            self._stderr_lines.append(line)

    def wait_ready(self, timeout: float = 5.0) -> bool:
        """Wait for network_sim to be in accept(). Reusable across reconnects."""
        result = self._ready_event.wait(timeout=timeout)
        self._ready_event.clear()
        return result

    def dump_stderr(self) -> str:
        """Return collected stderr as a string (for diagnostics on failure)."""
        return b''.join(self._stderr_lines).decode(errors='replace')


@pytest.fixture()
def sim_process(request):
    """Start network_sim as a subprocess, yield SimProcess."""
    if not NETWORK_SIM_BIN.exists():
        pytest.skip(f'network_sim not built at {NETWORK_SIM_BIN}')

    tcp_port = _find_free_tcp_port()
    frame_port = _find_free_udp_port()

    proc = subprocess.Popen(
        [
            str(NETWORK_SIM_BIN),
            '--tcp-port', str(tcp_port),
            '--frame-port', str(frame_port),
            '--strip-length', str(STRIP_LENGTH),
            '--device-id', '42',
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

    # Surface network_sim stderr on test failure for diagnostics
    rep = getattr(request.node, 'rep_call', None)
    if rep and rep.failed:
        stderr = sim.dump_stderr()
        if stderr:
            print(f'\n--- network_sim stderr ---\n{stderr}--- end ---')


class TestIntegration:
    def test_load_start_receive_frames(self, sim_process):
        tcp_port, frame_port = sim_process.tcp_port, sim_process.frame_port

        receiver = UdpFrameReceiver(frame_port)
        device_id = 42

        dev = NetworkDevice(
            device_id=device_id,
            host='127.0.0.1',
            tcp_port=tcp_port,
            device_type='sim',
            udp_receiver=receiver,
        )

        try:
            blob = _make_blob()
            assert dev.load(blob, gen=1), 'load should succeed'
            assert dev.state() == DeviceState.LOADED

            t0_us = time.monotonic_ns() // 1000
            dev.start(t0_us * 1000)  # start() expects ns
            assert dev.state() == DeviceState.PLAYING

            # Poll for frames with a timeout
            frames: list[DeviceFrame] = []
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and len(frames) < 3:
                receiver.poll()
                dev.tick_once(time.monotonic_ns())
                new = dev.drain_frames()
                frames.extend(new)
                if not new:
                    time.sleep(0.025)

            assert len(frames) >= 1, f'expected frames, got {len(frames)}'

            f = frames[0]
            assert f.gen == 1
            assert f.frame_index >= 0
            assert len(f.rgb) == STRIP_LENGTH * 3
            # Verify non-zero RGB (wave animation should produce color)
            assert any(b != 0 for b in f.rgb), 'expected non-zero RGB data'
        finally:
            receiver.close()

    def test_load_bad_blob_returns_nack(self, sim_process):
        tcp_port, frame_port = sim_process.tcp_port, sim_process.frame_port

        receiver = UdpFrameReceiver(frame_port)

        dev = NetworkDevice(
            device_id=42,
            host='127.0.0.1',
            tcp_port=tcp_port,
            device_type='sim',
            udp_receiver=receiver,
        )

        try:
            assert not dev.load(b'\x00\x01\x02', gen=1), 'bad blob should fail'
        finally:
            receiver.close()

    def test_pause_resume(self, sim_process):
        tcp_port, frame_port = sim_process.tcp_port, sim_process.frame_port

        receiver = UdpFrameReceiver(frame_port)

        dev = NetworkDevice(
            device_id=42,
            host='127.0.0.1',
            tcp_port=tcp_port,
            device_type='sim',
            udp_receiver=receiver,
        )

        try:
            blob = _make_blob()
            assert dev.load(blob, gen=1)

            t0_ns = time.monotonic_ns()
            dev.start(t0_ns)

            # Let it play for a bit
            time.sleep(0.1)
            receiver.poll()
            dev.tick_once(time.monotonic_ns())
            pre_pause = dev.drain_frames()

            # Pause
            dev.pause(time.monotonic_ns())
            assert dev.state() == DeviceState.PAUSED

            # Wait — no new frames should arrive (device is paused)
            time.sleep(0.1)
            receiver.poll()
            dev.tick_once(time.monotonic_ns())
            during_pause = dev.drain_frames()
            # Might get 0 or 1 frame from the tick boundary, but no stream
            pause_count = len(during_pause)

            # Resume
            t0_resume = time.monotonic_ns()
            dev.resume(t0_resume)
            assert dev.state() == DeviceState.PLAYING

            # Should get more frames
            frames: list[DeviceFrame] = []
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and len(frames) < 2:
                receiver.poll()
                dev.tick_once(time.monotonic_ns())
                frames.extend(dev.drain_frames())
                if not frames:
                    time.sleep(0.025)

            assert len(frames) >= 1
        finally:
            receiver.close()

    def test_stop_resets(self, sim_process):
        tcp_port, frame_port = sim_process.tcp_port, sim_process.frame_port

        receiver = UdpFrameReceiver(frame_port)

        dev = NetworkDevice(
            device_id=42,
            host='127.0.0.1',
            tcp_port=tcp_port,
            device_type='sim',
            udp_receiver=receiver,
        )

        try:
            blob = _make_blob()
            assert dev.load(blob, gen=1)

            dev.start(time.monotonic_ns())
            time.sleep(0.1)

            dev.stop()
            assert dev.state() == DeviceState.LOADED
        finally:
            receiver.close()

    def test_reconnect_after_disconnect(self, sim_process):
        tcp_port, frame_port = sim_process.tcp_port, sim_process.frame_port
        blob = _make_blob()

        # --- First connection: load, play, get frames ---
        receiver1 = UdpFrameReceiver(frame_port)
        dev1 = NetworkDevice(
            device_id=42,
            host='127.0.0.1',
            tcp_port=tcp_port,
            device_type='sim',
            udp_receiver=receiver1,
        )

        try:
            assert dev1.load(blob, gen=1)
            dev1.start(time.monotonic_ns())

            frames: list[DeviceFrame] = []
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and len(frames) < 1:
                receiver1.poll()
                dev1.tick_once(time.monotonic_ns())
                frames.extend(dev1.drain_frames())
                if not frames:
                    time.sleep(0.025)

            assert len(frames) >= 1, 'first connection: expected frames'
            assert frames[0].gen == 1
        finally:
            receiver1.close()

        # --- Disconnect: close the TCP socket ---
        dev1._disconnect()

        # Wait for network_sim to reset and re-enter accept()
        assert sim_process.wait_ready(timeout=5.0), \
            'network_sim did not become ready after disconnect'

        # --- Second connection: load with new gen, play, get frames ---
        receiver2 = UdpFrameReceiver(frame_port)
        dev2 = NetworkDevice(
            device_id=42,
            host='127.0.0.1',
            tcp_port=tcp_port,
            device_type='sim',
            udp_receiver=receiver2,
        )

        try:
            assert dev2.load(blob, gen=7), 'second load should succeed'
            dev2.start(time.monotonic_ns())

            frames2: list[DeviceFrame] = []
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and len(frames2) < 1:
                receiver2.poll()
                dev2.tick_once(time.monotonic_ns())
                frames2.extend(dev2.drain_frames())
                if not frames2:
                    time.sleep(0.025)

            assert len(frames2) >= 1, 'second connection: expected frames'
            assert frames2[0].gen == 7
        finally:
            receiver2.close()
