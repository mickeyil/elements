"""Controller-side UDP clock sync manager for ESP32 devices."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import socket
import time

from .device_protocol import encode_sync_req, parse_sync_resp

log = logging.getLogger(__name__)

_STARTUP_PROBE_COUNT = 8
_STARTUP_INTERVAL_NS = 1_000_000_000
_RECOVERY_INTERVAL_NS = 5_000_000_000
_STEADY_INTERVAL_NS = 15_000_000_000
_CONFIRM_PROBE_COUNT = 2
_CONFIRM_INTERVAL_NS = 250_000_000
_STALE_TIMEOUT_NS = 60_000_000_000
_RTT_MAX_US = 200_000
_BEST_SAMPLE_COUNT = 3
_MEDIAN_WINDOW_SIZE = 5
_MEDIAN_WINDOW_MIN = 3
_CORRECTION_DEADBAND_US = 2_000


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
    latest_smoothed_offset_us: int | None = None
    residual_offset_us: int | None = None
    rtt_us: int | None = None
    last_sample_ns: int | None = None
    last_correction_ns: int | None = None
    next_probe_ns: int = 0
    startup_probes_remaining: int = _STARTUP_PROBE_COUNT
    confirm_probes_remaining: int = 0
    median_window_offsets: list[int] = field(default_factory=list)
    median_window_rtts: list[int] = field(default_factory=list)
    prev_delta_sign: int = 0
    last_emitted_status: tuple[object, ...] | None = None


@dataclass
class SyncUpdate:
    device_id: int
    clock_state: str
    clock_offset_us: int | None
    rtt_us: int | None
    send_correction: bool
    seq: int | None = None
    boot_token: int | None = None
    correction_offset_us: int | None = None


@dataclass
class ClockSyncPollResult:
    status_updates: list[SyncUpdate] = field(default_factory=list)
    observed_activity_device_ids: set[int] = field(default_factory=set)


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
        if state is None or state.clock_state == 'pending':
            return {
                'clock_state': 'pending',
                'clock_offset_ms': None,
                'clock_rtt_ms': None,
                'clock_last_sync_age_s': None,
            }

        clock_offset_ms = None
        if (
            state.clock_state in {'synced', 'stale'}
            and state.residual_offset_us is not None
        ):
            clock_offset_ms = state.residual_offset_us / 1000.0

        return {
            'clock_state': state.clock_state,
            'clock_offset_ms': clock_offset_ms,
            'clock_rtt_ms': state.rtt_us / 1000.0 if state.rtt_us is not None else None,
            'clock_last_sync_age_s': (
                max(0.0, (now_ns - state.last_sample_ns) / 1e9)
                if state.last_sample_ns is not None
                else None
            ),
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

            if state.confirm_probes_remaining > 0:
                state.confirm_probes_remaining -= 1
                if state.confirm_probes_remaining > 0:
                    state.next_probe_ns = now_ns + _CONFIRM_INTERVAL_NS
                elif state.startup_probes_remaining > 0:
                    state.next_probe_ns = now_ns + _STARTUP_INTERVAL_NS
                else:
                    state.next_probe_ns = now_ns + self._steady_interval_for_state(state)
            elif state.startup_probes_remaining > 0:
                state.startup_probes_remaining -= 1
                if state.startup_probes_remaining > 0:
                    state.next_probe_ns = now_ns + _STARTUP_INTERVAL_NS
                else:
                    state.next_probe_ns = now_ns + self._steady_interval_for_state(state)
            else:
                state.next_probe_ns = now_ns + self._steady_interval_for_state(state)

    def poll(self) -> ClockSyncPollResult:
        result = ClockSyncPollResult()
        samples_by_device: dict[int, list[_SyncSample]] = {}
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
            pending = self._pending.get(seq)
            if pending is None or pending.t1_us != t1_us:
                continue

            state = self._states.get(pending.device_id)
            if state is None:
                continue
            self._pending.pop(seq, None)
            result.observed_activity_device_ids.add(pending.device_id)

            if state.boot_token != boot_token:
                state.boot_token = boot_token
                self._reset_state_for_new_boot(state)

            t4_us = self._clock_ns() // 1000
            rtt_us = (t4_us - t1_us) - (t3_us - t2_us)
            if rtt_us <= 0 or rtt_us > _RTT_MAX_US:
                continue
            offset_us = int(((t2_us - t1_us) + (t3_us - t4_us)) / 2)
            samples_by_device.setdefault(pending.device_id, []).append(
                _SyncSample(seq=seq, rtt_us=rtt_us, offset_us=offset_us)
            )

        now_ns = self._clock_ns()
        for device_id, round_samples in samples_by_device.items():
            state = self._states.get(device_id)
            if state is None:
                continue
            update = self._process_round_samples(device_id, state, round_samples, now_ns)
            if update is not None and self._should_emit_status_update(state, update):
                result.status_updates.append(update)

        for update in self._collect_stale_updates(now_ns):
            state = self._states.get(update.device_id)
            if state is not None and self._should_emit_status_update(state, update):
                result.status_updates.append(update)

        return result

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
    def _candidate_from_round(samples: list[_SyncSample]) -> tuple[int, int] | None:
        if not samples:
            return None
        best = sorted(samples, key=lambda sample: sample.rtt_us)[:min(_BEST_SAMPLE_COUNT, len(samples))]
        offsets = sorted(sample.offset_us for sample in best)
        rtts = sorted(sample.rtt_us for sample in best)
        return offsets[len(offsets) // 2], rtts[len(rtts) // 2]

    @staticmethod
    def _windowed_median(values: list[int]) -> int | None:
        if len(values) < _MEDIAN_WINDOW_MIN:
            return None
        ordered = sorted(values)
        return ordered[len(ordered) // 2]

    @staticmethod
    def _reset_state_for_new_boot(state: _SyncState) -> None:
        state.applied_offset_us = None
        state.latest_smoothed_offset_us = None
        state.residual_offset_us = None
        state.rtt_us = None
        state.last_sample_ns = None
        state.last_correction_ns = None
        state.next_probe_ns = 0
        state.startup_probes_remaining = _STARTUP_PROBE_COUNT
        state.confirm_probes_remaining = 0
        state.median_window_offsets.clear()
        state.median_window_rtts.clear()
        state.prev_delta_sign = 0
        state.clock_state = 'pending'
        state.last_emitted_status = None

    @staticmethod
    def _schedule_confirm_probes(state: _SyncState, now_ns: int) -> None:
        state.confirm_probes_remaining = _CONFIRM_PROBE_COUNT
        state.next_probe_ns = now_ns + _CONFIRM_INTERVAL_NS

    @staticmethod
    def _steady_interval_for_state(state: _SyncState) -> int:
        if state.clock_state == 'synced':
            return _STEADY_INTERVAL_NS
        return _RECOVERY_INTERVAL_NS

    def _process_round_samples(
        self,
        device_id: int,
        state: _SyncState,
        round_samples: list[_SyncSample],
        now_ns: int,
    ) -> SyncUpdate | None:
        candidate = self._candidate_from_round(round_samples)
        if candidate is None:
            return None

        candidate_offset_us, candidate_rtt_us = candidate
        state.median_window_offsets.append(candidate_offset_us)
        if len(state.median_window_offsets) > _MEDIAN_WINDOW_SIZE:
            state.median_window_offsets.pop(0)
        state.median_window_rtts.append(candidate_rtt_us)
        if len(state.median_window_rtts) > _MEDIAN_WINDOW_SIZE:
            state.median_window_rtts.pop(0)
        state.last_sample_ns = now_ns

        smoothed_offset_us = self._windowed_median(state.median_window_offsets)
        smoothed_rtt_us = self._windowed_median(state.median_window_rtts)
        if smoothed_offset_us is None or smoothed_rtt_us is None:
            state.rtt_us = candidate_rtt_us
            if state.clock_state == 'stale':
                state.clock_state = 'pending'
                return SyncUpdate(
                    device_id=device_id,
                    clock_state='pending',
                    clock_offset_us=None,
                    rtt_us=state.rtt_us,
                    send_correction=False,
                )
            return None

        state.latest_smoothed_offset_us = smoothed_offset_us
        state.rtt_us = smoothed_rtt_us

        if state.applied_offset_us is None:
            state.applied_offset_us = smoothed_offset_us
            state.residual_offset_us = None
            state.prev_delta_sign = 0
            state.clock_state = 'settling'
            state.last_correction_ns = now_ns
            self._schedule_confirm_probes(state, now_ns)
            return SyncUpdate(
                device_id=device_id,
                clock_state='settling',
                clock_offset_us=None,
                rtt_us=state.rtt_us,
                send_correction=True,
                seq=round_samples[-1].seq,
                boot_token=state.boot_token,
                correction_offset_us=smoothed_offset_us,
            )

        residual_offset_us = smoothed_offset_us - state.applied_offset_us
        state.residual_offset_us = residual_offset_us
        state.clock_state = 'synced'

        if abs(residual_offset_us) < _CORRECTION_DEADBAND_US:
            state.prev_delta_sign = 0
            return SyncUpdate(
                device_id=device_id,
                clock_state='synced',
                clock_offset_us=residual_offset_us,
                rtt_us=state.rtt_us,
                send_correction=False,
            )

        sign = 1 if residual_offset_us > 0 else -1
        if sign != state.prev_delta_sign:
            state.prev_delta_sign = sign
            log.debug(
                'sync: device %d correction deferred residual=%.1fms sign=%d',
                device_id,
                residual_offset_us / 1000.0,
                sign,
            )
            return SyncUpdate(
                device_id=device_id,
                clock_state='synced',
                clock_offset_us=residual_offset_us,
                rtt_us=state.rtt_us,
                send_correction=False,
            )

        state.applied_offset_us = smoothed_offset_us
        state.prev_delta_sign = 0
        state.clock_state = 'settling'
        state.last_correction_ns = now_ns
        self._schedule_confirm_probes(state, now_ns)
        return SyncUpdate(
            device_id=device_id,
            clock_state='settling',
            clock_offset_us=None,
            rtt_us=state.rtt_us,
            send_correction=True,
            seq=round_samples[-1].seq,
            boot_token=state.boot_token,
            correction_offset_us=smoothed_offset_us,
        )

    def _collect_stale_updates(self, now_ns: int) -> list[SyncUpdate]:
        updates: list[SyncUpdate] = []
        for device_id, state in self._states.items():
            if state.last_sample_ns is None or state.clock_state in {'pending', 'stale'}:
                continue
            if now_ns - state.last_sample_ns < _STALE_TIMEOUT_NS:
                continue
            state.clock_state = 'stale'
            state.median_window_offsets.clear()
            state.median_window_rtts.clear()
            state.prev_delta_sign = 0
            state.startup_probes_remaining = _STARTUP_PROBE_COUNT
            state.confirm_probes_remaining = 0
            state.next_probe_ns = now_ns
            log.debug('sync: device %d stale; restarting calibration burst', device_id)
            updates.append(
                SyncUpdate(
                    device_id=device_id,
                    clock_state='stale',
                    clock_offset_us=state.residual_offset_us,
                    rtt_us=state.rtt_us,
                    send_correction=False,
                )
            )
        return updates

    @staticmethod
    def _status_signature(update: SyncUpdate) -> tuple[object, ...]:
        offset_bucket = None
        if update.clock_offset_us is not None:
            offset_bucket = int(round(update.clock_offset_us / 100.0))
        return (update.clock_state, offset_bucket)

    def _should_emit_status_update(self, state: _SyncState, update: SyncUpdate) -> bool:
        signature = self._status_signature(update)
        if update.send_correction or signature != state.last_emitted_status:
            state.last_emitted_status = signature
            return True
        return False
