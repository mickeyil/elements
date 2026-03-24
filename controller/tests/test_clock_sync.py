"""Tests for controller-side clock sync transport and filtering."""

from __future__ import annotations

import socket
import struct

import pytest

from elemctl.clock_sync import ClockSyncManager
from elemctl.wire import SYNC_REQ_STRUCT


class _FakeClock:
    def __init__(self, now_ns: int = 0):
        self.now_ns = now_ns

    def __call__(self) -> int:
        return self.now_ns


def _recv_sync_req(sock: socket.socket) -> tuple[int, int, int]:
    data, _addr = sock.recvfrom(64)
    pkt_type, seq, boot_token, t1_us = SYNC_REQ_STRUCT.unpack(data)
    assert pkt_type == 0x01
    return seq, boot_token, t1_us


def _send_sync_resp(
    sock: socket.socket,
    manager: ClockSyncManager,
    *,
    seq: int,
    boot_token: int,
    t1_us: int,
    t2_us: int,
    t3_us: int,
) -> None:
    packet = struct.pack('<BHIqqq', 0x02, seq, boot_token, t1_us, t2_us, t3_us)
    sock.sendto(packet, ('127.0.0.1', manager.local_port))


def _drive_probe(
    udp: socket.socket,
    manager: ClockSyncManager,
    fake_clock: _FakeClock,
    *,
    device_id: int = 1,
    host: str = '127.0.0.1',
    boot_token: int,
    offset_us: int,
    rtt_us: int = 4_000,
) -> list:
    manager.send_due_probes([(device_id, host)], fake_clock.now_ns)
    seq, _boot_hint, t1_us = _recv_sync_req(udp)
    one_way_a = rtt_us // 2 + offset_us
    one_way_b = rtt_us // 2 - offset_us
    t2_us = t1_us + one_way_a
    t3_us = t2_us + 200
    t4_us = t1_us + one_way_a + 200 + one_way_b
    fake_clock.now_ns = t4_us * 1000
    _send_sync_resp(
        udp,
        manager,
        seq=seq,
        boot_token=boot_token,
        t1_us=t1_us,
        t2_us=t2_us,
        t3_us=t3_us,
    )
    updates = manager.poll()
    fake_clock.now_ns += 20_000_000_000
    return updates


class TestClockSyncManager:
    def test_initial_sync_waits_for_window_then_settles_and_syncs(self):
        fake_clock = _FakeClock(1_000_000_000)
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(('127.0.0.1', 0))
        udp.settimeout(1.0)
        manager = ClockSyncManager(udp.getsockname()[1], clock_ns=fake_clock)

        try:
            manager.on_connected(1)
            assert _drive_probe(
                udp, manager, fake_clock, boot_token=55, offset_us=100
            ) == []
            assert _drive_probe(
                udp, manager, fake_clock, boot_token=55, offset_us=100
            ) == []

            updates = _drive_probe(
                udp, manager, fake_clock, boot_token=55, offset_us=100
            )
            assert len(updates) == 1
            assert updates[0].device_id == 1
            assert updates[0].clock_state == 'settling'
            assert updates[0].clock_offset_us is None
            assert updates[0].send_correction is True
            assert updates[0].boot_token == 55
            assert updates[0].correction_offset_us == 100

            status = manager.clock_status(1, fake_clock.now_ns)
            assert status['clock_state'] == 'settling'
            assert status['clock_offset_ms'] is None

            updates = _drive_probe(
                udp, manager, fake_clock, boot_token=55, offset_us=100
            )
            assert len(updates) == 1
            assert updates[0].clock_state == 'synced'
            assert updates[0].clock_offset_us == 0
            assert updates[0].send_correction is False

            status = manager.clock_status(1, fake_clock.now_ns)
            assert status['clock_state'] == 'synced'
            assert status['clock_offset_ms'] == pytest.approx(0.0)
            assert status['clock_rtt_ms'] == pytest.approx(4.0)
        finally:
            manager.close()
            udp.close()

    def test_windowed_filter_and_sustained_guard_delay_correction(self):
        fake_clock = _FakeClock(1_000_000_000)
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(('127.0.0.1', 0))
        udp.settimeout(1.0)
        manager = ClockSyncManager(udp.getsockname()[1], clock_ns=fake_clock)

        try:
            manager.on_connected(1)
            assert _drive_probe(
                udp, manager, fake_clock, boot_token=77, offset_us=0, rtt_us=10_000
            ) == []
            assert _drive_probe(
                udp, manager, fake_clock, boot_token=77, offset_us=0, rtt_us=10_000
            ) == []

            updates = _drive_probe(
                udp, manager, fake_clock, boot_token=77, offset_us=0, rtt_us=10_000
            )
            assert len(updates) == 1
            assert updates[0].send_correction is True
            assert updates[0].correction_offset_us == 0

            updates = _drive_probe(
                udp, manager, fake_clock, boot_token=77, offset_us=0, rtt_us=10_000
            )
            assert len(updates) == 1
            assert updates[0].clock_state == 'synced'
            assert updates[0].clock_offset_us == 0
            assert updates[0].send_correction is False

            assert _drive_probe(
                udp, manager, fake_clock, boot_token=77, offset_us=3_000, rtt_us=10_000
            ) == []
            assert _drive_probe(
                udp, manager, fake_clock, boot_token=77, offset_us=3_000, rtt_us=10_000
            ) == []

            updates = _drive_probe(
                udp, manager, fake_clock, boot_token=77, offset_us=3_000, rtt_us=10_000
            )
            assert len(updates) == 1
            assert updates[0].clock_state == 'synced'
            assert updates[0].clock_offset_us == 3_000
            assert updates[0].send_correction is False

            updates = _drive_probe(
                udp, manager, fake_clock, boot_token=77, offset_us=3_000, rtt_us=10_000
            )
            assert len(updates) == 1
            assert updates[0].clock_state == 'settling'
            assert updates[0].clock_offset_us is None
            assert updates[0].send_correction is True
            assert updates[0].correction_offset_us == 3_000
        finally:
            manager.close()
            udp.close()

    def test_stale_sync_emits_state_and_restarts_calibration(self):
        fake_clock = _FakeClock(1_000_000_000)
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(('127.0.0.1', 0))
        udp.settimeout(1.0)
        manager = ClockSyncManager(udp.getsockname()[1], clock_ns=fake_clock)

        try:
            manager.on_connected(1)
            _drive_probe(udp, manager, fake_clock, boot_token=91, offset_us=100)
            _drive_probe(udp, manager, fake_clock, boot_token=91, offset_us=100)
            _drive_probe(udp, manager, fake_clock, boot_token=91, offset_us=100)
            _drive_probe(udp, manager, fake_clock, boot_token=91, offset_us=100)

            fake_clock.now_ns += 61_000_000_000
            updates = manager.poll()
            assert len(updates) == 1
            assert updates[0].clock_state == 'stale'
            assert updates[0].send_correction is False

            status = manager.clock_status(1, fake_clock.now_ns)
            assert status['clock_state'] == 'stale'

            updates = _drive_probe(
                udp, manager, fake_clock, boot_token=91, offset_us=100
            )
            assert len(updates) == 1
            assert updates[0].clock_state == 'pending'
            assert updates[0].send_correction is False

            status = manager.clock_status(1, fake_clock.now_ns)
            assert status['clock_state'] == 'pending'
        finally:
            manager.close()
            udp.close()
