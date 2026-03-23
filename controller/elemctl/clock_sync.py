"""Controller-side UDP clock sync manager for ESP32 devices."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import socket
import time

from .wire import encode_sync_req, parse_sync_resp

log = logging.getLogger(__name__)

_STARTUP_PROBE_COUNT = 8
_STARTUP_INTERVAL_NS = 1_000_000_000
_STEADY_INTERVAL_NS = 15_000_000_000
_RTT_MAX_US = 200_000
_BEST_SAMPLE_COUNT = 3
_SAMPLE_WINDOW = 8


@dataclass
class _PendingProbe:
    device_id: int
    t1_us: int


@dataclass
class _SyncSample:
    seq: int
    rtt_us: int
    offset_us: int


@dataclass
class _SyncState:
    clock_state: str = 'pending'
    boot_token: int | None = None
    applied_offset_us: int | None = None
    display_offset_us: int | None = None
    rtt_us: int | None = None
    last_sync_ns: int | None = None
    next_probe_ns: int = 0
    startup_probes_remaining: int = _STARTUP_PROBE_COUNT
    samples: list[_SyncSample] = field(default_factory=list)
    last_applied_seq: int = 0


@dataclass
class SyncUpdate:
    device_id: int
    seq: int
    boot_token: int
    applied_offset_us: int
    display_offset_us: int
    rtt_us: int


class ClockSyncManager:
    """Maintains per-device sync probes and filtered offset estimates."""

    def __init__(self, sync_port: int, clock_ns=time.monotonic_ns):
        self._sync_port = sync_port
        self._clock_ns = clock_ns
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.bind(('', 0))
        self._sock.setblocking(False)
        self._states: dict[int, _SyncState] = {}
        self._pending: dict[int, _PendingProbe] = {}
        self._next_seq = 1

    def close(self) -> None:
        self._sock.close()

    def prune_device_ids(self, valid_ids: set[int]) -> None:
        self._states = {
            device_id: state
            for device_id, state in self._states.items()
            if device_id in valid_ids
        }
        self._pending = {
            seq: probe
            for seq, probe in self._pending.items()
            if probe.device_id in valid_ids
        }

    def on_connected(self, device_id: int) -> None:
        self._states[device_id] = _SyncState()

    def on_disconnected(self, device_id: int) -> None:
        self._states.pop(device_id, None)
        self._pending = {
            seq: probe
            for seq, probe in self._pending.items()
            if probe.device_id != device_id
        }

    def clock_status(self, device_id: int, now_ns: int) -> dict[str, object]:
        state = self._states.get(device_id)
        if state is None or state.applied_offset_us is None or state.last_sync_ns is None:
            return {
                'clock_state': 'pending',
                'clock_drift_ms': None,
                'clock_rtt_ms': None,
                'clock_last_sync_age_s': None,
            }
        return {
            'clock_state': state.clock_state,
            'clock_drift_ms': (state.display_offset_us or 0) / 1000.0,
            'clock_rtt_ms': state.rtt_us / 1000.0 if state.rtt_us is not None else None,
            'clock_last_sync_age_s': max(0.0, (now_ns - state.last_sync_ns) / 1e9),
        }

    def send_due_probes(self, targets: list[tuple[int, str]], now_ns: int | None = None) -> None:
        if now_ns is None:
            now_ns = self._clock_ns()
        for device_id, host in targets:
            if not host:
                continue
            state = self._states.get(device_id)
            if state is None:
                continue
            if now_ns < state.next_probe_ns:
                continue
            seq = self._allocate_seq()
            t1_us = now_ns // 1000
            boot_token_hint = state.boot_token or 0
            packet = encode_sync_req(seq, boot_token_hint, t1_us)
            try:
                self._sock.sendto(packet, (host, self._sync_port))
            except OSError as e:
                log.warning(
                    'sync: send probe to %s:%d for device %d failed: %s',
                    host,
                    self._sync_port,
                    device_id,
                    e,
                )
                continue
            self._pending[seq] = _PendingProbe(device_id=device_id, t1_us=t1_us)
            if state.startup_probes_remaining > 0:
                state.startup_probes_remaining -= 1
                if state.startup_probes_remaining > 0:
                    state.next_probe_ns = now_ns + _STARTUP_INTERVAL_NS
                else:
                    state.next_probe_ns = now_ns + _STEADY_INTERVAL_NS
            else:
                state.next_probe_ns = now_ns + _STEADY_INTERVAL_NS

    def poll(self) -> list[SyncUpdate]:
        updates: list[SyncUpdate] = []
        while True:
            try:
                data, _addr = self._sock.recvfrom(256)
            except BlockingIOError:
                break
            except OSError as e:
                log.warning('sync: recv failed: %s', e)
                break

            parsed = parse_sync_resp(data)
            if parsed is None:
                continue
            seq, boot_token, t1_us, t2_us, t3_us = parsed
            pending = self._pending.pop(seq, None)
            if pending is None or pending.t1_us != t1_us:
                continue

            state = self._states.get(pending.device_id)
            if state is None:
                continue

            if state.boot_token != boot_token:
                state.boot_token = boot_token
                state.applied_offset_us = None
                state.display_offset_us = None
                state.rtt_us = None
                state.last_sync_ns = None
                state.samples.clear()
                state.last_applied_seq = 0
                state.clock_state = 'pending'

            t4_us = self._clock_ns() // 1000
            rtt_us = (t4_us - t1_us) - (t3_us - t2_us)
            if rtt_us <= 0 or rtt_us > _RTT_MAX_US:
                continue
            offset_us = int(((t2_us - t1_us) + (t3_us - t4_us)) / 2)

            state.samples.append(_SyncSample(seq=seq, rtt_us=rtt_us, offset_us=offset_us))
            if len(state.samples) > _SAMPLE_WINDOW:
                state.samples.pop(0)

            filtered = self._filtered_offset(state.samples)
            if filtered is None:
                continue
            filtered_offset_us, filtered_rtt_us = filtered
            state.last_sync_ns = self._clock_ns()
            display_offset_us = 0 if state.applied_offset_us is None else filtered_offset_us - state.applied_offset_us

            if (
                state.clock_state != 'synced'
                or state.applied_offset_us != filtered_offset_us
                or state.rtt_us != filtered_rtt_us
            ):
                state.clock_state = 'synced'
                state.applied_offset_us = filtered_offset_us
                state.display_offset_us = display_offset_us
                state.rtt_us = filtered_rtt_us
                state.last_applied_seq = seq
                updates.append(SyncUpdate(
                    device_id=pending.device_id,
                    seq=seq,
                    boot_token=boot_token,
                    applied_offset_us=filtered_offset_us,
                    display_offset_us=display_offset_us,
                    rtt_us=filtered_rtt_us,
                ))

        return updates

    @property
    def local_port(self) -> int:
        return self._sock.getsockname()[1]

    def _allocate_seq(self) -> int:
        start = self._next_seq
        while True:
            seq = self._next_seq & 0xFFFF
            if seq == 0:
                self._next_seq = 1
                seq = 1
            else:
                self._next_seq += 1
            if seq not in self._pending:
                return seq
            if self._next_seq == start:
                raise RuntimeError('no free sync sequence ids')

    @staticmethod
    def _filtered_offset(samples: list[_SyncSample]) -> tuple[int, int] | None:
        if not samples:
            return None
        best = sorted(samples, key=lambda sample: sample.rtt_us)[:min(_BEST_SAMPLE_COUNT, len(samples))]
        offsets = sorted(sample.offset_us for sample in best)
        rtts = sorted(sample.rtt_us for sample in best)
        return offsets[len(offsets) // 2], rtts[len(rtts) // 2]
