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

from .types import PI, SecMarker, PixelGroup, StripDef, AnimDef, CompiledManifest
from .compiler import compile_program, compile_manifest

# Re-export for `from elements.dsl import *`
__all__ = ["PI", "sec", "strip", "wave", "shift", "spark", "paint", "pacifica",
           "build", "build_manifest", "CompiledManifest"]


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
        self.configured_strip_lengths: dict[str, int] | None = None

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

def strip(name: str, length: int | None = None, type: str = "RGB") -> StripDef:
    if length is None:
        configured = _builder.configured_strip_lengths
        if configured is None:
            raise ValueError(
                f"length required for strip {name!r} outside config-backed compile"
            )
        resolved = configured.get(name)
        if resolved is None:
            raise ValueError(f"unknown configured strip {name!r}")
        length = resolved
    else:
        configured = _builder.configured_strip_lengths
        if configured is not None and name in configured and length > configured[name]:
            raise ValueError(
                f"strip {name!r} length {length} exceeds configured length "
                f"{configured[name]}"
            )

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


def paint(**params) -> AnimDef:
    return _make_anim("paint", params)


def pacifica(**params) -> AnimDef:
    return _make_anim("pacifica", params)


def build(beat: float, duration: float,
          target_fps: int = 50, requires_sync: bool = False) -> dict[str, bytes]:
    """Compile the accumulated program. Returns one binary blob per strip."""
    try:
        return compile_program(_builder.strips, _builder.events, beat, duration,
                               target_fps=target_fps, requires_sync=requires_sync)
    finally:
        _builder.reset()


def build_manifest(beat: float, duration: float,
                   target_fps: int = 50, requires_sync: bool = False) -> CompiledManifest:
    """Compile the accumulated program. Returns manifest with blobs + safe intervals."""
    try:
        return compile_manifest(_builder.strips, _builder.events, beat, duration,
                                target_fps=target_fps, requires_sync=requires_sync)
    finally:
        _builder.reset()
