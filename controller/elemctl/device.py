from enum import IntEnum
from dataclasses import dataclass
from typing import Protocol


class DeviceState(IntEnum):
    IDLE = 0
    LOADED = 1
    PLAYING = 2
    PAUSED = 3
    ENDED = 4


@dataclass
class DeviceFrame:
    gen: int          # u16, echoed from LOAD/JUMP
    frame_index: int  # u32, monotonic per device, reset on LOAD/JUMP
    t_rel: float      # seconds relative to program start
    rgb: bytes        # raw RGB bytes


class ControllerDevice(Protocol):
    """Protocol for devices managed by the controller.

    All time-sensitive methods receive now_ns from the controller,
    which owns the clock.
    """

    # Commands
    def load(self, blob: bytes, gen: int) -> bool: ...
    def start(self, t0_ns: int) -> None: ...
    def jump(self, t0_ns: int, t_rel: float, gen: int) -> None: ...
    def pause(self, now_ns: int) -> None: ...
    def resume(self, t0_ns: int) -> None: ...
    def stop(self) -> None: ...

    # Per-frame tick
    def tick_once(self, now_ns: int) -> None: ...

    # Queries
    def state(self) -> DeviceState: ...
    def current_t_rel(self, now_ns: int) -> float: ...

    # Frame output
    def drain_frames(self) -> list[DeviceFrame]: ...

    # Capability
    def supports_debug_seek(self) -> bool: ...
    def debug_seek(self, t_rel: float, now_ns: int) -> None: ...
