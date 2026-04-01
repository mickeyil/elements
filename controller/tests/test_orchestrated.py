"""Orchestrated end-to-end test: controller API → ControllerService → NetworkDevice → network_sim.

Starts a real controller server + ControllerService backed by real network_sim
subprocesses. Validates that the full stack (controller protocol → service →
TCP/UDP → sim) works together correctly.
"""

import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

pytestmark = pytest.mark.runtime_integration

_repo = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo / 'compiler'))

from elemctl.config import Config, DeviceConfig
from elemctl.server import ControllerServer
from elemctl.service import ControllerService
from elemctl.controller_protocol import KIND_FRAME, KIND_JSON, parse_json_payload

from .sim_helpers import find_free_udp_port, start_sim, stop_sim
from .controller_helpers import ControllerClient, wait_for_socket

STRIP_LENGTH = 10
FADE = 1.0
PROGRAM_DURATION = 1.0

# ---------------------------------------------------------------------------
# DSL sources
# ---------------------------------------------------------------------------

SYMMETRIC_DSL = """\
from elements.dsl import strip, spark, sec
s1 = strip('strip_a', length=10)
s2 = strip('strip_b', length=10)
sp = spark(color='white', fade=1.0)
sp.schedule(s1.pixels('0-9'), at=0, duration=sec(1.0))
sp.schedule(s2.pixels('0-9'), at=0, duration=sec(1.0))
"""

OFFSET_DSL = """\
from elements.dsl import strip, spark, sec
s1 = strip('strip_a', length=10)
s2 = strip('strip_b', length=10)
sp = spark(color='white', fade=1.0)
sp.schedule(s1.pixels('0-9'), at=0, duration=sec(1.0))
sp.schedule(s2.pixels('0-9'), at=sec(0.2), duration=sec(0.8))
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class PlaybackResult:
    frames: list[tuple[int, float, list[bytes]]]  # (frame_index, t_rel, strips)
    events: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    ended: bool = False


def _parse_frame(payload: bytes, n_strips: int, strip_length: int):
    """Parse a KIND_FRAME payload into (frame_index, t_rel, [strip_rgb...])."""
    expected = 8 + n_strips * strip_length * 3
    assert len(payload) == expected, f'frame payload {len(payload)} != {expected}'
    frame_index, t_rel = struct.unpack_from('<If', payload, 0)
    rgb_data = payload[8:]
    stride = strip_length * 3
    strips = [rgb_data[i * stride:(i + 1) * stride] for i in range(n_strips)]
    return frame_index, t_rel, strips


def _wait_for_reply(client: ControllerClient, cmd_id: int, result: PlaybackResult,
                    timeout: float = 3.0) -> dict:
    """Receive messages until we find a reply with the given id.

    Non-reply messages are ingested into *result*. Fails if reply not found
    or ok==false.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        msgs = client.recv_messages(timeout=min(0.25, max(0.05, deadline - time.monotonic())))
        for kind, payload in msgs:
            if kind == KIND_JSON:
                obj = parse_json_payload(payload)
                if obj.get('type') == 'reply' and obj.get('id') == cmd_id:
                    assert obj['ok'], f"command {cmd_id} failed: {obj.get('error')}"
                    return obj
            # Ingest everything that isn't our reply (events, frames, other replies)
            _ingest_message(kind, payload, result)
    pytest.fail(
        f'no reply for command id={cmd_id} within {timeout}s; '
        f'events={result.events}, errors={result.errors}'
    )


def _load_play_collect(client: ControllerClient, dsl: str, beat: float,
                       duration: float, timeout: float = 5.0) -> PlaybackResult:
    """Send load+play over the controller API, collect until state→ended or timeout."""
    result = PlaybackResult(frames=[])

    # 1. Load
    client.send_cmd({'id': 1, 'cmd': 'load', 'source': dsl,
                     'beat': beat, 'duration': duration})
    _wait_for_reply(client, cmd_id=1, result=result)

    # 2. Play
    client.send_cmd({'id': 2, 'cmd': 'play'})
    _wait_for_reply(client, cmd_id=2, result=result)

    # 3. Collect frames + events until ended or timeout
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not result.ended:
        msgs = client.recv_messages(timeout=min(0.25, deadline - time.monotonic()))
        for kind, payload in msgs:
            _ingest_message(kind, payload, result)

    return result


def _ingest_message(kind: int, payload: bytes, result: PlaybackResult) -> None:
    """Route a single controller-protocol message into a PlaybackResult."""
    if kind == KIND_FRAME:
        frame = _parse_frame(payload, n_strips=2, strip_length=STRIP_LENGTH)
        result.frames.append(frame)
    elif kind == KIND_JSON:
        obj = parse_json_payload(payload)
        if obj.get('type') == 'event':
            result.events.append(obj)
            if obj.get('event') == 'error':
                result.errors.append(obj)
            if obj.get('event') == 'state' and obj.get('state') == 'ended':
                result.ended = True


def _strip_mean(rgb_bytes: bytes) -> float:
    """Mean byte value across all channels in a strip."""
    return sum(rgb_bytes) / len(rgb_bytes)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def orchestrated(request, tmp_path):
    """Start 2 network_sims + ControllerServer, yield (socket_path, sim1, sim2)."""
    socket_path = str(tmp_path / 'ctrl.sock')

    # 1. Ports
    frame_port = find_free_udp_port()

    # 2. Start sims
    sim1 = start_sim(0, frame_port, STRIP_LENGTH, device_id=1)
    try:
        sim2 = start_sim(0, frame_port, STRIP_LENGTH, device_id=2)
    except Exception:
        stop_sim(sim1)
        raise

    # 3. Config + Service + ControllerServer
    server = None
    thread = None
    try:
        config = Config(
            frame_port=frame_port,
            devices=[
                DeviceConfig(
                    device_id=1, device_uid='sim-1', device_type='sim',
                    host='127.0.0.1', tcp_port=sim1.tcp_port,
                    strip_id='strip_a', length=STRIP_LENGTH,
                ),
                DeviceConfig(
                    device_id=2, device_uid='sim-2', device_type='sim',
                    host='127.0.0.1', tcp_port=sim2.tcp_port,
                    strip_id='strip_b', length=STRIP_LENGTH,
                ),
            ],
        )
        service = ControllerService(config)
        server = ControllerServer(service, socket_path)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        assert wait_for_socket(socket_path), 'controller server did not create socket'
    except Exception:
        if server:
            server.shutdown()
        if thread:
            thread.join(timeout=3.0)
        stop_sim(sim1)
        stop_sim(sim2)
        raise

    yield socket_path, sim1, sim2

    # Teardown: always stop sims regardless of server thread state
    server.shutdown()
    thread.join(timeout=3.0)
    thread_exited = not thread.is_alive()

    stop_sim(sim1)
    stop_sim(sim2)

    assert thread_exited, 'controller server thread did not exit after shutdown'

    # Dump sim stderr on failure
    rep = getattr(request.node, 'rep_call', None)
    if rep and rep.failed:
        for label, sim in [('sim1', sim1), ('sim2', sim2)]:
            stderr = sim.dump_stderr()
            if stderr:
                print(f'\n--- {label} stderr ---\n{stderr}--- end ---')


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestOrchestrated:
    def test_status_reports_two_connected(self, orchestrated):
        """Connect, drain snapshot, send status — both devices connected."""
        socket_path, _, _ = orchestrated
        client = ControllerClient(socket_path)
        try:
            # Drain snapshot
            msgs = client.recv_messages(timeout=2.0)
            assert len(msgs) >= 1, 'no snapshot received on connect'
            snap = parse_json_payload(msgs[0][1])
            assert snap['event'] == 'snapshot'
            assert len(snap['devices']) == 2

            # Explicit status command
            client.send_cmd({'id': 1, 'cmd': 'status'})
            dummy = PlaybackResult(frames=[])
            reply = _wait_for_reply(client, cmd_id=1, result=dummy)
            status = reply['result']
            assert status['event'] == 'snapshot'
            assert len(status['devices']) == 2
            for dev in status['devices']:
                assert dev['connected'] is True, \
                    f"device {dev['device_id']} not connected"

        finally:
            client.close()

    def test_symmetric_spark_playback_and_end(self, orchestrated):
        """Both strips get the same spark — brightness fades, strips match."""
        socket_path, _, _ = orchestrated
        client = ControllerClient(socket_path)
        try:
            # Drain snapshot
            client.recv_messages(timeout=2.0)

            result = _load_play_collect(
                client, SYMMETRIC_DSL, beat=1.0, duration=PROGRAM_DURATION,
                timeout=5.0,
            )
            assert not result.errors, f'unexpected errors: {result.errors}'
            assert result.ended, 'controller did not reach ended'
            assert len(result.frames) >= 3, \
                f'expected >= 3 frames, got {len(result.frames)}'

            # Both strips should match within ±2 per byte. The configure
            # handshake and simulator-side logging add a small extra skew
            # window around initial playback start.
            for fi, t_rel, strips in result.frames:
                assert len(strips) == 2
                for j in range(len(strips[0])):
                    diff = abs(strips[0][j] - strips[1][j])
                    assert diff <= 2, \
                        f'byte {j} differs by {diff} at frame_index={fi}, ' \
                        f't_rel={t_rel:.3f}'

            # Brightness must decrease over time (spark fades out)
            means = [_strip_mean(strips[0]) for _, _, strips in result.frames]
            for i in range(1, len(means)):
                assert means[i] <= means[i - 1] + 1, \
                    f'brightness increased: {means[i - 1]:.1f} -> {means[i]:.1f}'
        finally:
            client.close()

    def test_offset_spark_preserves_strip_identity(self, orchestrated):
        """Strip B starts 0.2s later — strip A is always dimmer when both lit."""
        socket_path, _, _ = orchestrated
        client = ControllerClient(socket_path)
        try:
            # Drain snapshot
            client.recv_messages(timeout=2.0)

            result = _load_play_collect(
                client, OFFSET_DSL, beat=1.0, duration=PROGRAM_DURATION,
                timeout=5.0,
            )
            assert not result.errors, f'unexpected errors: {result.errors}'
            assert result.ended, 'controller did not reach ended'

            # Find frames where both strips are lit
            both_lit = [
                (fi, t_rel, strips) for fi, t_rel, strips in result.frames
                if _strip_mean(strips[0]) > 5 and _strip_mean(strips[1]) > 5
            ]
            assert len(both_lit) >= 1, \
                f'no frames with both strips lit; means: ' \
                f'{[(_strip_mean(s[0]), _strip_mean(s[1])) for _, _, s in result.frames[:5]]}'

            # Strip A started first → further into fade → dimmer
            for fi, t_rel, strips in both_lit:
                mean_a = _strip_mean(strips[0])
                mean_b = _strip_mean(strips[1])
                assert mean_a < mean_b, \
                    f'strip_a ({mean_a:.1f}) should be dimmer than strip_b ' \
                    f'({mean_b:.1f}) at frame_index={fi}'
        finally:
            client.close()
