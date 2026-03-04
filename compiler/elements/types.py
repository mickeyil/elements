"""Shared types for the Elements DSL and compiler."""

from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Any

PI = math.pi

# ---------------------------------------------------------------------------
# Animation type constants
# ---------------------------------------------------------------------------

ANIM_TYPES = {"wave": 0, "shift": 1, "spark": 2, "fill": 3}

# Which animation params are time-based (beats → seconds)
TIME_PARAMS = {
    "wave":  ["period"],
    "spark": ["fade"],
    "shift": [],  # velocity is pixels/beat → pixels/sec, handled specially
}

# Required params per animation type
REQUIRED_PARAMS = {
    "wave":  ["channel", "h", "s", "v", "min_val", "max_val", "period", "phase0", "pixel_step"],
    "spark": ["color", "fade"],
    "shift": ["direction", "velocity"],
    "fill":  ["color"],
}

# Which animation types need work buffers
STATEFUL_TYPES = {"shift"}

# Channel name → uint8
CHANNELS = {"H": 0, "S": 1, "V": 2}

# Direction name → uint8
DIRECTIONS = {"left": 0, "right": 1}


# ---------------------------------------------------------------------------
# Lazy time marker
# ---------------------------------------------------------------------------

class SecMarker:
    """Lazy marker for seconds — resolved at compile time."""
    def __init__(self, seconds: float):
        self.seconds = seconds

    def __repr__(self):
        return f"sec({self.seconds})"


# ---------------------------------------------------------------------------
# Color constants
# ---------------------------------------------------------------------------

COLORS = {
    "white":       (0.0, 0.0, 1.0),      # H, S, V
    "yellow":      (60.0, 1.0, 1.0),
    "red":         (0.0, 1.0, 1.0),
    "green":       (120.0, 1.0, 1.0),
    "blue":        (240.0, 1.0, 1.0),
    "cyan":        (180.0, 1.0, 1.0),
    "magenta":     (300.0, 1.0, 1.0),
    "transparent": (0.0, 0.0, 0.0),
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

def _parse_indices(s: str) -> list[int]:
    """Parse index string: '0-9' → [0..9], '0,4' → [0,4], '0,2-5,9' → [0,2,3,4,5,9]"""
    indices = []
    for part in s.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            indices.extend(range(int(lo), int(hi) + 1))
        else:
            indices.append(int(part))
    return indices


@dataclass
class PixelGroup:
    strip_name: str
    indices: list[int]


@dataclass
class StripDef:
    name: str
    length: int
    type: str = "RGB"

    def pixels(self, indices_str: str) -> PixelGroup:
        return PixelGroup(self.name, _parse_indices(indices_str))


@dataclass
class AnimDef:
    anim_type: str
    params: dict[str, Any]
    _builder: Any = field(default=None, repr=False)

    def schedule(self, pixels: PixelGroup, at, duration, **kw):
        self._builder.add_event(self, pixels, at, duration, **kw)
