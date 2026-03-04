"""Elements v2 DSL — public API.

Usage:
    from elements.dsl import *

    def program(beat, duration):
        strip1 = strip("main", length=10, type="RGB")
        ...
        return build(beat=beat, duration=duration)
"""

from __future__ import annotations
from typing import Any

from .types import PI, SecMarker, PixelGroup, StripDef, AnimDef
from .compiler import compile_program

# Re-export for `from elements.dsl import *`
__all__ = ["PI", "sec", "strip", "wave", "shift", "spark", "fill", "build"]


def sec(value: float) -> SecMarker:
    return SecMarker(value)


# ---------------------------------------------------------------------------
# Hidden global builder
# ---------------------------------------------------------------------------

class _ProgramBuilder:
    def __init__(self):
        self.reset()

    def reset(self):
        self.strips: list[StripDef] = []
        self.animations: list[AnimDef] = []
        self.events: list[dict] = []

    def add_strip(self, s: StripDef):
        self.strips.append(s)

    def add_animation(self, a: AnimDef):
        self.animations.append(a)

    def add_event(self, anim: AnimDef, pixels: PixelGroup, at, duration, **kw):
        self.events.append({
            "anim": anim,
            "pixels": pixels,
            "at": at,
            "duration": duration,
            **kw,
        })


_builder = _ProgramBuilder()


# ---------------------------------------------------------------------------
# DSL public functions
# ---------------------------------------------------------------------------

def strip(name: str, length: int, type: str = "RGB") -> StripDef:
    s = StripDef(name, length, type)
    _builder.add_strip(s)
    return s


def _make_anim(anim_type: str, params: dict) -> AnimDef:
    a = AnimDef(anim_type, params, _builder=_builder)
    _builder.add_animation(a)
    return a


def wave(**params) -> AnimDef:
    return _make_anim("wave", params)


def shift(**params) -> AnimDef:
    return _make_anim("shift", params)


def spark(**params) -> AnimDef:
    return _make_anim("spark", params)


def fill(**params) -> AnimDef:
    return _make_anim("fill", params)


def build(beat: float, duration: float) -> dict[str, bytes]:
    """Compile the accumulated program. Returns one binary blob per strip."""
    try:
        return compile_program(_builder.strips, _builder.events, beat, duration)
    finally:
        _builder.reset()
