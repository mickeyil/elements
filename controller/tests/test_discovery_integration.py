"""Integration test: discovery-based device bring-up.

Starts a ControllerService with discovery enabled, starts network_sim
subprocesses with --discovery-port, and verifies that devices come online
dynamically, then loads/plays a spark program and verifies frames.
"""

import struct
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.runtime_integration

_repo = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_repo / 'compiler'))

from elemctl.config import Config, DeviceConfig
from elemctl.server import UdsServer
from elemctl.service import ControllerService
from elemctl.uds_wire import KIND_FRAME, KIND_JSON, parse_json_payload

from .sim_helpers import (
    find_free_udp_port,
    start_sim,
    stop_sim,
)
from .uds_helpers import UdsClient, wait_for_socket

STRIP_LENGTH = 10


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
sp.schedule(s1.pixels('0-9'), at=0.0, duration=sec(1.0))
sp.schedule(s2.pixels('0-9'), at=sec(0.3), duration=sec(1.0))
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_frame(payload: bytes, n_strips: int, strip_length: int):
    expected = 8 + n_strips * strip_length * 3
    assert len(payload) == expected, f'frame payload {len(payload)} != {expected}'
    frame_index, t_rel = struct.unpack_from('<If', payload, 0)
    rgb_data = payload[8:]
    stride = strip_length * 3
    strips = [rgb_data[i * stride:(i + 1) * stride] for i in range(n_strips)]
    return frame_index, t_rel, strips


def _strip_mean(rgb: bytes) -> float:
    return sum(rgb) / len(rgb)


def _wait_for_reply(client: UdsClient, cmd_id: int,
                    timeout: float = 5.0) -> dict:
    """Receive until we find the reply with the given id."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        msgs = client.recv_messages(
            timeout=min(0.5, max(0.05, deadline - time.monotonic())),
        )
        for kind, payload in msgs:
            if kind == KIND_JSON:
                obj = parse_json_payload(payload)
                if obj.get('type') == 'reply' and obj.get('id') == cmd_id:
                    return obj
    pytest.fail(f'no reply for command id={cmd_id} within {timeout}s')


def _wait_for_online(client: UdsClient, expected: int,
                     timeout: float = 10.0) -> list[dict]:
    """Poll status until online_count == expected. Returns device_status events."""
    deadline = time.monotonic() + timeout
    online_count = 0
    device_status_events: list[dict] = []
    status_cmd_id = 100

    def _ingest(msgs):
        nonlocal online_count
        for kind, payload in msgs:
            if kind != KIND_JSON:
                continue
            obj = parse_json_payload(payload)
            if obj.get('event') == 'device_status':
                device_status_events.append(obj)
            elif obj.get('type') == 'reply' and obj.get('id') == status_cmd_id:
                online_count = obj.get('result', {}).get('online_count', online_count)
            elif obj.get('event') == 'snapshot':
                online_count = obj.get('online_count', online_count)

    while time.monotonic() < deadline and online_count < expected:
        _ingest(client.recv_messages(timeout=0.5))
        client.send_cmd({'id': status_cmd_id, 'cmd': 'status'})
        _ingest(client.recv_messages(timeout=0.5))

    assert online_count == expected, (
        f'expected online_count={expected}, got {online_count}; '
        f'device_status_events={device_status_events}'
    )
    return device_status_events


def _load_play_collect(client: UdsClient, dsl: str, duration: float,
                       timeout: float = 8.0):
    """Load+play, collect frames until ended or timeout.

    Returns (frames, ended, errors) where errors is a list of error event dicts.
    """
    client.send_cmd({
        'id': 1, 'cmd': 'load',
        'source': dsl, 'beat': 1.0, 'duration': duration,
    })
    load_reply = _wait_for_reply(client, 1)
    assert load_reply['ok'], f"load failed: {load_reply.get('error')}"

    client.send_cmd({'id': 2, 'cmd': 'play'})
    play_reply = _wait_for_reply(client, 2)
    assert play_reply['ok'], f"play failed: {play_reply.get('error')}"

    frames = []
    errors = []
    ended = False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not ended:
        msgs = client.recv_messages(
            timeout=min(0.5, max(0.05, deadline - time.monotonic())),
        )
        for kind, payload in msgs:
            if kind == KIND_FRAME:
                frames.append(
                    _parse_frame(payload, n_strips=2, strip_length=STRIP_LENGTH),
                )
            elif kind == KIND_JSON:
                obj = parse_json_payload(payload)
                if obj.get('event') == 'state' and obj.get('state') == 'ended':
                    ended = True
                elif obj.get('event') == 'error':
                    errors.append(obj)

    return frames, ended, errors


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
def discovered(tmp_path):
    """Start controller with unresolved devices, start 2 sims with discovery,
    wait for both to come online, yield (client, device_status_events).

    Teardown stops everything."""
    socket_path = str(tmp_path / 'ctrl.sock')

    frame_port = find_free_udp_port()
    discovery_port = find_free_udp_port()

    config = Config(
        frame_port=frame_port,
        discovery_port=discovery_port,
        devices=[
            DeviceConfig(
                device_id=1, device_uid='sim-1', device_type='sim',
                host='', tcp_port=0,
                strip_id='strip_a', length=STRIP_LENGTH,
            ),
            DeviceConfig(
                device_id=2, device_uid='sim-2', device_type='sim',
                host='', tcp_port=0,
                strip_id='strip_b', length=STRIP_LENGTH,
            ),
        ],
    )

    service = ControllerService(config)
    server = UdsServer(service, socket_path)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    assert wait_for_socket(socket_path), 'UDS server did not create socket'

    sims = []
    client = None
    try:
        sim1 = start_sim(
            0, frame_port, STRIP_LENGTH, device_id=1,
            discovery_port=discovery_port, discovery_host='127.0.0.1',
            device_uid='sim-1',
        )
        sims.append(sim1)
        sim2 = start_sim(
            0, frame_port, STRIP_LENGTH, device_id=2,
            discovery_port=discovery_port, discovery_host='127.0.0.1',
            device_uid='sim-2',
        )
        sims.append(sim2)

        client = UdsClient(socket_path, timeout=5.0)
        device_status_events = _wait_for_online(client, expected=2)
    except Exception:
        if client:
            client.close()
        server.shutdown()
        thread.join(timeout=3.0)
        for sim in sims:
            stop_sim(sim)
        raise

    yield client, device_status_events

    client.close()
    server.shutdown()
    thread.join(timeout=3.0)
    for sim in sims:
        stop_sim(sim)
    assert not thread.is_alive(), 'server thread did not exit'


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDiscoveryIntegration:
    def test_discovery_brings_devices_online(self, discovered):
        """Devices come online via discovery. Load+play produces frames."""
        client, device_status_events = discovered

        # At least one device_status connected=true event
        connected_events = [
            e for e in device_status_events if e.get('connected') is True
        ]
        assert len(connected_events) >= 1, (
            f'expected device_status connected=true, got {device_status_events}'
        )

        # Explicit status round-trip
        client.send_cmd({'id': 200, 'cmd': 'status'})
        status_reply = _wait_for_reply(client, 200)
        assert status_reply['ok']
        status = status_reply['result']
        assert status['online_count'] == 2
        for dev in status['devices']:
            assert dev['connected'] is True, (
                f"device {dev['device_id']} not connected"
            )

        # Load + play symmetric spark, verify frames arrive with decaying brightness
        frames, ended, errors = _load_play_collect(client, SYMMETRIC_DSL, duration=1.0)
        assert not errors, f'unexpected errors: {errors}'
        assert ended, 'controller did not reach ended'
        assert len(frames) >= 2, f'expected >= 2 frames, got {len(frames)}'

        means = [_strip_mean(strips[0]) for _, _, strips in frames]
        for i in range(1, len(means)):
            assert means[i] <= means[i - 1] + 1, (
                f'brightness increased: {means[i-1]:.1f} -> {means[i]:.1f}'
            )

    def test_discovery_preserves_strip_identity(self, discovered):
        """Offset spark proves discovery mapped uid→strip correctly.

        strip_a starts at t=0, strip_b at t=0.3. When both are lit,
        strip_a is further into the fade → dimmer.
        """
        client, _ = discovered

        frames, ended, errors = _load_play_collect(client, OFFSET_DSL, duration=1.3)
        assert not errors, f'unexpected errors: {errors}'
        assert ended, 'controller did not reach ended'
        assert len(frames) >= 2, f'expected >= 2 frames, got {len(frames)}'

        # Find frames where both strips are lit
        both_lit = [
            (fi, t_rel, strips) for fi, t_rel, strips in frames
            if _strip_mean(strips[0]) > 5 and _strip_mean(strips[1]) > 5
        ]
        assert len(both_lit) >= 1, (
            f'no frames with both strips lit; means: '
            f'{[(_strip_mean(s[0]), _strip_mean(s[1])) for _, _, s in frames[:5]]}'
        )

        # strip_a started first → further into fade → dimmer
        for fi, t_rel, strips in both_lit:
            mean_a = _strip_mean(strips[0])
            mean_b = _strip_mean(strips[1])
            assert mean_a < mean_b, (
                f'strip_a ({mean_a:.1f}) should be dimmer than strip_b '
                f'({mean_b:.1f}) at frame_index={fi}, t_rel={t_rel:.3f}'
            )
