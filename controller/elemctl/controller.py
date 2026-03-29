from __future__ import annotations

import time
from dataclasses import dataclass
from enum import IntEnum
from typing import Callable

from elements.types import CompiledManifest

from .device import ControllerDevice, DeviceFrame


class ControllerState(IntEnum):
    IDLE = 0
    LOADED = 1
    PLAYING = 2
    PAUSED = 3
    STOPPED = 4
    ENDED = 5


@dataclass
class ProgramFrame:
    frame_index: int
    t_rel: float
    strips: list[bytes]  # canonical strip order


@dataclass
class ControllerEvent:
    class Kind(IntEnum):
        SESSION_STARTED = 0
        STATE_CHANGED = 1
        LOOPED = 2
        ERROR = 3

    kind: Kind
    state: ControllerState
    session_id: int = 0
    epoch: int = 0
    message: str = ""


@dataclass
class StripConfig:
    strip_id: str
    length: int
    device: ControllerDevice


@dataclass
class _Bucket:
    frame_index: int
    t_rel: float
    strips: list[bytes | None]
    present: int = 0


class Controller:
    def __init__(
        self,
        strips: list[StripConfig],
        clock: Callable[[], int] = time.monotonic_ns,
    ):
        self._strips = list(strips)
        self._strip_id_to_indices: dict[str, list[int]] = {}
        seen_devices: dict[int, str] = {}
        for i, s in enumerate(self._strips):
            if s.device is None:
                raise ValueError(f"strip {s.strip_id!r} has no device")
            device_oid = id(s.device)
            prev = seen_devices.get(device_oid)
            if prev is not None:
                raise ValueError(
                    "Controller currently assumes one strip per device; "
                    f"device object reused for strips {prev!r} and {s.strip_id!r}"
                )
            seen_devices[device_oid] = s.strip_id
            self._strip_id_to_indices.setdefault(s.strip_id, []).append(i)

        self._clock = clock

        self._state = ControllerState.IDLE
        self._session_id = 0
        self._epoch = 0
        self._gen = 0
        self._duration = 0.0
        self._paused_t_rel = 0.0
        self._loop = False
        self._safe_intervals: list[tuple[float, float]] = []

        self._active_strips: list[StripConfig] = []
        self._slot_for_active: list[int] = []
        self._session_manifest_strips: list[tuple[str, int]] = []
        self._session_target_groups: list[list[int]] = []
        self._expected_gen: list[int] = []
        self._buckets: dict[int, _Bucket] = {}

        self._program_frames: list[ProgramFrame] = []
        self._program_frame_stream_enabled = False
        self._events: list[ControllerEvent] = []

    # ------------------------------------------------------------------
    # Read-only properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> ControllerState:
        return self._state

    @property
    def session_id(self) -> int:
        return self._session_id

    @property
    def epoch(self) -> int:
        return self._epoch

    @property
    def gen(self) -> int:
        return self._gen

    @property
    def duration(self) -> float:
        return self._duration

    @property
    def current_t_rel(self) -> float:
        if self._state in (
            ControllerState.IDLE,
            ControllerState.LOADED,
            ControllerState.STOPPED,
        ):
            return 0.0
        if self._state == ControllerState.PAUSED:
            return self._paused_t_rel
        if self._state == ControllerState.PLAYING:
            now = self._clock()
            return self._max_current_t_rel(now)
        if self._state == ControllerState.ENDED:
            return self._duration
        return 0.0

    @property
    def safe_intervals(self) -> list[tuple[float, float]]:
        return list(self._safe_intervals)

    @property
    def session_strips(self) -> list[tuple[str, int]]:
        return list(self._session_manifest_strips)

    @property
    def session_target_groups(self) -> list[list[int]]:
        return [list(group) for group in self._session_target_groups]

    @property
    def program_frame_stream_enabled(self) -> bool:
        return self._program_frame_stream_enabled

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    def load(
        self,
        manifest: CompiledManifest,
        loop: bool = False,
        target_groups: list[list[int]] | None = None,
    ) -> bool:
        if not self._strips:
            self._queue_event(ControllerEvent.Kind.ERROR, "no configured strips")
            return False

        if target_groups is None:
            if len(manifest.strips) != len(self._strips):
                self._queue_event(ControllerEvent.Kind.ERROR, "strip count mismatch")
                return False

            target_groups = []
            seen = [False] * len(self._strips)
            for ms in manifest.strips:
                canonical_indices = self._strip_id_to_indices.get(ms.strip_id)
                if canonical_indices is None:
                    self._queue_event(
                        ControllerEvent.Kind.ERROR,
                        f"unknown strip_id: {ms.strip_id}",
                    )
                    return False
                if len(canonical_indices) != 1:
                    self._queue_event(
                        ControllerEvent.Kind.ERROR,
                        f"duplicate strip_id requires targeted load support: {ms.strip_id}",
                    )
                    return False
                ci = canonical_indices[0]
                if seen[ci]:
                    self._queue_event(
                        ControllerEvent.Kind.ERROR,
                        f"duplicate strip_id: {ms.strip_id}",
                    )
                    return False
                seen[ci] = True
                if ms.length > self._strips[ci].length:
                    self._queue_event(
                        ControllerEvent.Kind.ERROR,
                        f"strip length exceeds configured length for "
                        f"{self._strips[ci].strip_id}",
                    )
                    return False
                target_groups.append([ci])
        elif len(target_groups) != len(manifest.strips):
            self._queue_event(
                ControllerEvent.Kind.ERROR,
                "target group count mismatch",
            )
            return False

        seen_targets: set[int] = set()
        new_active_strips: list[StripConfig] = []
        new_slot_for_active: list[int] = []
        new_active_device_ids: set[int] = set()
        for slot, (ms, group) in enumerate(zip(manifest.strips, target_groups)):
            if not group:
                self._queue_event(
                    ControllerEvent.Kind.ERROR,
                    f"missing targets for strip: {ms.strip_id}",
                )
                return False
            for ci in group:
                if not (0 <= ci < len(self._strips)):
                    self._queue_event(
                        ControllerEvent.Kind.ERROR,
                        f"target index out of range: {ci}",
                    )
                    return False
                if ci in seen_targets:
                    self._queue_event(
                        ControllerEvent.Kind.ERROR,
                        f"duplicate target index: {ci}",
                    )
                    return False
                if ms.length > self._strips[ci].length:
                    self._queue_event(
                        ControllerEvent.Kind.ERROR,
                        f"strip length exceeds configured length for "
                        f"{self._strips[ci].strip_id}",
                    )
                    return False
                seen_targets.add(ci)
                new_active_strips.append(self._strips[ci])
                new_slot_for_active.append(slot)
                new_active_device_ids.add(id(self._strips[ci].device))

        new_session_manifest_strips = [(ms.strip_id, ms.length) for ms in manifest.strips]
        new_session_target_groups = [list(group) for group in target_groups]
        prev_active_strips = list(self._active_strips)
        prev_active_device_ids = {id(s.device) for s in prev_active_strips}
        overlaps_previous_session = bool(prev_active_device_ids & new_active_device_ids)

        # Attempt device loads with provisional gen
        new_gen = self._peek_next_gen()
        loaded_active: list[int] = []

        for ai, active_strip in enumerate(new_active_strips):
            slot = new_slot_for_active[ai]
            blob = manifest.strips[slot].blob
            if not active_strip.device.load(blob, new_gen):
                for lai in loaded_active:
                    new_active_strips[lai].device.stop()
                if overlaps_previous_session:
                    for strip in prev_active_strips:
                        strip.device.stop()
                    self._state = ControllerState.IDLE
                    self._reset_active_runtime()
                self._queue_event(
                    ControllerEvent.Kind.ERROR,
                    f"device load failed for {active_strip.strip_id}",
                )
                return False
            loaded_active.append(ai)

        # All devices loaded — commit identity
        stale_previous_strips = [
            strip for strip in prev_active_strips
            if id(strip.device) not in new_active_device_ids
        ]
        self._session_id += 1
        self._epoch = 0
        self._gen = new_gen
        self._duration = manifest.duration
        self._loop = loop
        self._safe_intervals = list(manifest.safe_intervals)
        self._paused_t_rel = 0.0
        self._buckets.clear()
        self._program_frames = []
        self._active_strips = new_active_strips
        self._slot_for_active = new_slot_for_active
        self._session_manifest_strips = new_session_manifest_strips
        self._session_target_groups = new_session_target_groups
        self._expected_gen = [self._gen] * len(self._active_strips)
        self._program_frame_stream_enabled = all(
            s.device.produces_program_frames()
            for s in self._active_strips
        )

        self._state = ControllerState.LOADED
        for strip in stale_previous_strips:
            strip.device.stop()
        self._queue_event(ControllerEvent.Kind.SESSION_STARTED)
        self._queue_event(ControllerEvent.Kind.STATE_CHANGED)
        return True

    def abort(self, reason: str) -> None:
        if self._state == ControllerState.IDLE:
            return

        for s in self._active_strips:
            if getattr(s.device, 'is_connected', True):
                s.device.stop()

        self._reset_active_runtime()
        self._state = ControllerState.IDLE
        self._queue_event(ControllerEvent.Kind.ERROR, reason)
        self._queue_event(ControllerEvent.Kind.STATE_CHANGED)

    def play(self) -> None:
        if self._state in (ControllerState.IDLE, ControllerState.PLAYING):
            return

        if self._state == ControllerState.PAUSED:
            now = self._clock()
            t0 = now - int(self._paused_t_rel * 1e9)
            for s in self._active_strips:
                s.device.resume(t0)
            self._state = ControllerState.PLAYING
            self._queue_event(ControllerEvent.Kind.STATE_CHANGED)
            return

        # From LOADED, STOPPED, or ENDED
        t0 = self._clock()
        for s in self._active_strips:
            s.device.start(t0)
        self._epoch += 1
        self._state = ControllerState.PLAYING
        self._queue_event(ControllerEvent.Kind.STATE_CHANGED)

    def pause(self) -> None:
        if self._state != ControllerState.PLAYING:
            return

        now = self._clock()
        for s in self._active_strips:
            s.device.pause(now)

        self._paused_t_rel = self._max_current_t_rel(now)
        self._state = ControllerState.PAUSED
        self._queue_event(ControllerEvent.Kind.STATE_CHANGED)

    def seek(self, t_rel: float) -> None:
        if self._state in (ControllerState.IDLE, ControllerState.STOPPED):
            return

        if not self._safe_intervals:
            self._queue_event(
                ControllerEvent.Kind.ERROR, "seek requires safe intervals"
            )
            return

        snapped = self._snap_to_safe(t_rel)
        was_playing = self._state == ControllerState.PLAYING

        self._epoch += 1
        self._advance_gen()
        now = self._clock()
        t0 = now - int(snapped * 1e9)
        for i, s in enumerate(self._active_strips):
            s.device.jump(t0, snapped, self._gen)
            self._expected_gen[i] = self._gen
        self._buckets.clear()

        if not was_playing:
            self._paused_t_rel = self._max_current_t_rel(now)
            self._state = ControllerState.PAUSED
        self._queue_event(ControllerEvent.Kind.STATE_CHANGED)

    def debug_seek(self, t_rel: float) -> None:
        if self._state in (ControllerState.IDLE, ControllerState.STOPPED):
            return

        for s in self._active_strips:
            if not s.device.supports_debug_seek():
                self._queue_event(
                    ControllerEvent.Kind.ERROR,
                    "debug_seek requires debug_seek support",
                )
                return

        self._epoch += 1
        now = self._clock()
        for s in self._active_strips:
            s.device.debug_seek(t_rel, now)
        self._buckets.clear()

        if self._state == ControllerState.PLAYING:
            pass  # stay PLAYING
        else:
            self._paused_t_rel = self._max_current_t_rel(now)
            self._state = ControllerState.PAUSED
        self._queue_event(ControllerEvent.Kind.STATE_CHANGED)

    def stop(self) -> None:
        if self._state == ControllerState.IDLE:
            return

        for s in self._active_strips:
            s.device.stop()
        self._buckets.clear()
        self._state = ControllerState.STOPPED
        self._queue_event(ControllerEvent.Kind.STATE_CHANGED)

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    def tick_once(self) -> None:
        if self._state == ControllerState.IDLE:
            return

        now = self._clock()

        # 1. Tick each device
        for s in self._strips:
            s.device.tick_once(now)

        # 2. Drain each unique device exactly once, then assemble only the
        # active-device frames into logical buckets.
        drained_by_device: dict[int, list[DeviceFrame]] = {}
        for s in self._strips:
            dev_oid = id(s.device)
            if dev_oid not in drained_by_device:
                drained_by_device[dev_oid] = s.device.drain_frames()

        for i, s in enumerate(self._active_strips):
            dev_oid = id(s.device)
            drained = drained_by_device.get(dev_oid, [])
            if not self._program_frame_stream_enabled:
                continue

            for frame in drained:
                if frame.gen != self._expected_gen[i]:
                    continue

                slot = self._slot_for_active[i]
                bucket = self._buckets.get(frame.frame_index)
                if bucket is None:
                    bucket = _Bucket(
                        frame_index=frame.frame_index,
                        t_rel=frame.t_rel,
                        strips=[None] * len(self._session_manifest_strips),
                    )
                    self._buckets[frame.frame_index] = bucket

                if bucket.strips[slot] is None:
                    bucket.present += 1
                    bucket.strips[slot] = frame.rgb

        # 3. Emit complete program frames
        complete = []
        for fi, bucket in self._buckets.items():
            if bucket.present == len(self._session_manifest_strips):
                self._program_frames.append(
                    ProgramFrame(
                        frame_index=bucket.frame_index,
                        t_rel=bucket.t_rel,
                        strips=[s for s in bucket.strips],  # all non-None
                    )
                )
                complete.append(fi)
        for fi in complete:
            del self._buckets[fi]

        # 4. End-of-program detection (controller-inferred, not device-reported)
        if self._state == ControllerState.PLAYING:
            all_past_end = bool(self._active_strips) and all(
                s.device.current_t_rel(now) >= self._duration
                for s in self._active_strips
            )
            if all_past_end:
                if self._loop:
                    self._epoch += 1
                    self._advance_gen()
                    self._buckets.clear()
                    now = self._clock()
                    # Devices are ENDED here. jump() transitions ENDED→PAUSED,
                    # so resume() is needed to restart playback. This differs
                    # from seek-while-PLAYING where jump() keeps devices PLAYING.
                    for i, s in enumerate(self._active_strips):
                        s.device.jump(now, 0.0, self._gen)
                        s.device.resume(now)
                        self._expected_gen[i] = self._gen
                    self._queue_event(ControllerEvent.Kind.LOOPED)
                else:
                    self._state = ControllerState.ENDED
                    self._queue_event(ControllerEvent.Kind.STATE_CHANGED)

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def drain_program_frames(self) -> list[ProgramFrame]:
        """Drain assembled program frames.

        Queue ownership is intentionally asymmetric:

        - session-invalidating operations such as load/abort clear the queue
        - within-session operations such as stop/seek/debug_seek do not

        That keeps already assembled frames available to consumers until they
        are explicitly drained.
        """
        out = self._program_frames
        self._program_frames = []
        return out

    def drain_events(self) -> list[ControllerEvent]:
        out = self._events
        self._events = []
        return out

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _queue_event(self, kind: ControllerEvent.Kind, message: str = "") -> None:
        self._events.append(
            ControllerEvent(
                kind=kind,
                state=self._state,
                session_id=self._session_id,
                epoch=self._epoch,
                message=message,
            )
        )

    def _peek_next_gen(self) -> int:
        next_gen = (self._gen + 1) & 0xFFFF
        if next_gen == 0:
            next_gen = 1
        return next_gen

    def _advance_gen(self) -> int:
        self._gen = self._peek_next_gen()
        return self._gen

    def _reset_active_runtime(self) -> None:
        self._duration = 0.0
        self._paused_t_rel = 0.0
        self._loop = False
        self._safe_intervals = []
        self._active_strips = []
        self._slot_for_active = []
        self._session_manifest_strips = []
        self._session_target_groups = []
        self._expected_gen = []
        self._buckets.clear()
        self._program_frames = []
        self._program_frame_stream_enabled = False

    def uses_device(self, device: ControllerDevice) -> bool:
        return any(s.device is device for s in self._active_strips)

    def _max_current_t_rel(self, now: int) -> float:
        if not self._active_strips:
            return 0.0
        return max(s.device.current_t_rel(now) for s in self._active_strips)

    def _snap_to_safe(self, t_rel: float) -> float:
        if not self._safe_intervals:
            return 0.0
        for lo, hi in reversed(self._safe_intervals):
            if lo <= t_rel < hi:
                return t_rel
            if hi <= t_rel:
                return lo
        return self._safe_intervals[0][0]
