"""End-to-end integration test: network_sim subprocess + NetworkDevice.

Starts a network_sim C++ process, connects via NetworkDevice, loads a
compiled blob, starts playback, and verifies that UDP frames arrive.
"""

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

from .sim_helpers import find_free_tcp_port, find_free_udp_port, start_sim, stop_sim

STRIP_LENGTH = 5


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


@pytest.fixture()
def sim_process(request):
    """Start network_sim as a subprocess, yield SimProcess."""
    tcp_port = find_free_tcp_port()
    frame_port = find_free_udp_port()

    sim = start_sim(tcp_port, frame_port, STRIP_LENGTH, device_id=42)
    yield sim
    stop_sim(sim)

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
            strip_length=STRIP_LENGTH,
            frame_port=frame_port,
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
            strip_length=STRIP_LENGTH,
            frame_port=frame_port,
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
            strip_length=STRIP_LENGTH,
            frame_port=frame_port,
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
            strip_length=STRIP_LENGTH,
            frame_port=frame_port,
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
            strip_length=STRIP_LENGTH,
            frame_port=frame_port,
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
            strip_length=STRIP_LENGTH,
            frame_port=frame_port,
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
