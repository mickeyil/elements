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


class TestClockSyncManager:
    def test_probe_round_trip_produces_sync_update(self):
        fake_clock = _FakeClock(1_000_000_000)
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(('127.0.0.1', 0))
        udp.settimeout(1.0)
        manager = ClockSyncManager(udp.getsockname()[1], clock_ns=fake_clock)

        try:
            manager.on_connected(1)
            manager.send_due_probes([(1, '127.0.0.1')], fake_clock.now_ns)
            seq, _boot_hint, t1_us = _recv_sync_req(udp)

            t2_us = t1_us + 1_000
            t3_us = t2_us + 200
            t4_us = t1_us + 2_000
            fake_clock.now_ns = t4_us * 1000
            _send_sync_resp(
                udp,
                manager,
                seq=seq,
                boot_token=55,
                t1_us=t1_us,
                t2_us=t2_us,
                t3_us=t3_us,
            )

            updates = manager.poll()
            assert len(updates) == 1
            assert updates[0].device_id == 1
            assert updates[0].clock_state == 'settling'
            assert updates[0].clock_offset_us is None
            assert updates[0].send_correction is True
            assert updates[0].boot_token == 55
            assert updates[0].correction_offset_us == 100
            assert updates[0].rtt_us == 1800

            status = manager.clock_status(1, fake_clock.now_ns)
            assert status['clock_state'] == 'settling'
            assert status['clock_offset_ms'] is None
            assert status['clock_rtt_ms'] == pytest.approx(1.8)

            fake_clock.now_ns += 250_000_000
            manager.send_due_probes([(1, '127.0.0.1')], fake_clock.now_ns)
            seq, _boot_hint, t1_us = _recv_sync_req(udp)

            t2_us = t1_us + 1_200
            t3_us = t2_us + 200
            t4_us = t1_us + 2_200
            fake_clock.now_ns = t4_us * 1000
            _send_sync_resp(
                udp,
                manager,
                seq=seq,
                boot_token=55,
                t1_us=t1_us,
                t2_us=t2_us,
                t3_us=t3_us,
            )

            updates = manager.poll()
            assert len(updates) == 1
            assert updates[0].clock_state == 'synced'
            assert updates[0].clock_offset_us == 100
            assert updates[0].send_correction is False

            status = manager.clock_status(1, fake_clock.now_ns)
            assert status['clock_state'] == 'synced'
            assert status['clock_offset_ms'] == pytest.approx(0.1)
            assert status['clock_rtt_ms'] == pytest.approx(2.0)
        finally:
            manager.close()
            udp.close()

    def test_filter_uses_median_of_lowest_rtt_samples(self):
        fake_clock = _FakeClock(1_000_000_000)
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(('127.0.0.1', 0))
        udp.settimeout(1.0)
        manager = ClockSyncManager(udp.getsockname()[1], clock_ns=fake_clock)

        samples = [
            (4_000, 1_000),
            (4_000, 1_200),
            (4_000, 1_400),
            (20_000, 5_000),
        ]

        try:
            manager.on_connected(1)
            for rtt_us, offset_us in samples:
                manager.send_due_probes([(1, '127.0.0.1')], fake_clock.now_ns)
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
                    boot_token=77,
                    t1_us=t1_us,
                    t2_us=t2_us,
                    t3_us=t3_us,
                )
                manager.poll()
                fake_clock.now_ns += 1_000_000_000

            status = manager.clock_status(1, fake_clock.now_ns)
            assert status['clock_state'] == 'synced'
            assert status['clock_offset_ms'] == pytest.approx(0.2)
        finally:
            manager.close()
            udp.close()
