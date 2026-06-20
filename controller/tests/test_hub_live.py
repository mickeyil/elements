"""Live hub test: the full join cycle against the real sim binary.

Drives one sim_device process through discover, register, idle
survival (controller pings vs the device's liveness timeout), a
status query, and a reboot with re-registration. This is the test
that proves the wire layer against the reference implementation, so
it favors observable behavior over speed; expect ~20 seconds.
"""

import os
import subprocess
import time

import pytest

from elemctl import wire
from elemctl.hub import DeviceConnected, DeviceDisconnected, DeviceHub
from elemctl.sim import (
    SIM_DEVICE_BIN,
    SIM_REBOOT_EXIT_CODE,
    STORAGE_ROOT_ENV,
    build_sim_command,
)

pytestmark = pytest.mark.runtime_integration

UID = 'sim-hubtest'


@pytest.fixture
def hub():
    # 6040/6043 are compile-time well-known on the device; the link and
    # frame ports travel in the OFFER and argv, so they can be ephemeral.
    h = DeviceHub(discovery_port=6040, link_port=0, frame_port=0, sync_port=6043)
    yield h
    h.close()


@pytest.fixture
def sim_factory(hub, tmp_path):
    if not SIM_DEVICE_BIN.is_file():
        pytest.skip(f'sim_device not built at {SIM_DEVICE_BIN}')

    procs = []
    env = os.environ.copy()
    env[STORAGE_ROOT_ENV] = str(tmp_path)
    cmd = build_sim_command(UID, controller_host='127.0.0.1',
                            frame_port=hub.frame_port)

    def launch():
        proc = subprocess.Popen(cmd, env=env)
        procs.append(proc)
        return proc

    yield launch
    for proc in procs:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=5.0)


def run_hub(hub, condition, timeout_s):
    """Poll until condition(events) or timeout. Returns all events."""
    events = []
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        events.extend(hub.poll({UID}).events)
        if condition(events):
            return events
        time.sleep(0.005)
    return events


def wait_for_connect(hub, *, timeout_s=10.0):
    events = run_hub(hub, lambda e: any(
        isinstance(ev, DeviceConnected) for ev in e), timeout_s)
    connects = [ev for ev in events if isinstance(ev, DeviceConnected)]
    assert connects, f'device never registered; events: {events}'
    return connects[-1]


def test_full_join_cycle(hub, sim_factory):
    proc = sim_factory()

    # Discover, connect out, register.
    connected = wait_for_connect(hub)
    assert connected.uid == UID
    assert connected.boot_token != 0
    first_boot_token = connected.boot_token

    # Idle survival: the device drops a quiet controller after
    # 2 x PING_INTERVAL_MS, so outlasting that proves our pings flow.
    idle_s = 2 * wire.PING_INTERVAL_MS / 1000 + 1
    events = run_hub(hub, lambda e: any(
        isinstance(ev, DeviceDisconnected) for ev in e), timeout_s=idle_s)
    assert not any(isinstance(ev, DeviceDisconnected) for ev in events), events
    assert hub.is_connected(UID)

    # A real command round-trip.
    acks = []
    assert hub.send(UID, wire.encode_query_device_status(), acks.append)
    run_hub(hub, lambda e: bool(acks), timeout_s=5.0)
    assert acks and acks[0].status == wire.ACK_OK
    report = wire.parse_device_status(acks[0].payload)
    assert report.mode in wire.DEVICE_MODE_NAMES

    # Reboot: ACK first, then the process exits with the sentinel and
    # the hub sees the connection drop.
    reboot_acks = []
    assert hub.send(UID, wire.encode_reboot(), reboot_acks.append)
    events = run_hub(hub, lambda e: any(
        isinstance(ev, DeviceDisconnected) for ev in e), timeout_s=10.0)
    assert reboot_acks and reboot_acks[0].status == wire.ACK_OK
    assert any(isinstance(ev, DeviceDisconnected) for ev in events), events
    assert proc.wait(timeout=5.0) == SIM_REBOOT_EXIT_CODE

    # Fresh process, fresh boot_token: the hub reports a reboot.
    sim_factory()
    reconnected = wait_for_connect(hub)
    assert reconnected.rebooted is True
    assert reconnected.boot_token != first_boot_token
