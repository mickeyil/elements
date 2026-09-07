#!/usr/bin/env python3
"""Generate the test_handoff_*.bin fixtures: source-to-dependent handoffs.

Each program hands one event's pixels to a later stateful event. The C++
tests render every program under several frame schedules (dense, offset,
stalled, late first frame) and expect the same output from all of them,
because the engine samples a source at its end boundary rather than at
whatever frame last happened to draw it.

All programs use beat 1.0, so the times below are milliseconds. Grayscale
paints are (h=0, s=0, v), so RGB = round(v * 255) on every channel.

skipped_direct (1 px, 800 ms)
  Paint white [120,180); Shift v=0 [200,600) source=paint.
  Expected: white during the shift, even when no frame fell inside the
  paint.

skipped_copy (1 px, 800 ms)
  As above plus Paint v=0.4 [180,200), which overwrites the white paint's
  buffer, so the compiler preserves white with a copy op at 180.
  Expected: 102 during [180,200), white during the shift.

wave_endpoint (1 px, 800 ms)
  V Wave 0..1, period 400, phase pi/2 over [0,400): 255 at 0, 0 at 200,
  back to 255 at 400. Shift v=0 [400,800) source=wave.
  Expected: 255 during the shift, the wave's value at its end.

shift_chain (4 px, 800 ms)
  Paint v=.2,.4,.6,.8 [0,100); Shift A right 10 px/s circular [100,300)
  source=paint; Shift B v=0 [300,600) on pixels 1-2, source=A.
  A's endpoint is shifted by 2 pixels: [.6,.8,.2,.4]. B reads slots 1-2.
  Expected: t=200 [204,51,102,153]; during B [0,204,51,0].

self_source (1 px, 600 ms)
  Paint white [0,100); Shift v=0 [100,300) with no source=, so it reads
  its own layer buffer, which holds the paint's endpoint.
  Expected: white during the shift.

slot_reuse (1 px, 1000 ms)
  L0: red [0,100), blue [100,200), Shift1 v=0 [200,700) source=red.
  L1: green [0,300), yellow [300,400), Shift2 v=0 [400,600) source=green.
  The two preserve copies (at 100 and 300) share one pool slot. Shift1 must
  snapshot red at 200, before copy2 reuses the slot at 300.
  Expected: yellow at 350, green at 450 (Shift2 on top), red at 650
  (Shift1 alone).
"""

import sys
from pathlib import Path
from elements.dsl import *
from elements.types import AnimDef


def _out_dir() -> Path:
    """Output directory: argv[1] if given (CMake passes the build dir), else here."""
    d = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
    d.mkdir(parents=True, exist_ok=True)
    return d


def _gray(v: float) -> tuple[float, float, float]:
    return (0.0, 0.0, v)


def _hold() -> AnimDef:
    """A shift that freezes its source and holds it."""
    return shift(direction="right", velocity=0, circular=True, fill="transparent")


def skipped_direct() -> bytes:
    s = strip("test", length=1)
    px = s.pixels("0")
    white = paint(colors=[_gray(1.0)], format="hsv")
    white.schedule(px, at=sec(0.12), duration=sec(0.06))
    _hold().schedule(px, at=sec(0.2), duration=sec(0.4), source=white)
    return build(beat=1.0, duration=0.8)["test"]


def skipped_copy() -> bytes:
    s = strip("test", length=1)
    px = s.pixels("0")
    white = paint(colors=[_gray(1.0)], format="hsv")
    white.schedule(px, at=sec(0.12), duration=sec(0.06))
    dim = paint(colors=[_gray(0.4)], format="hsv")
    dim.schedule(px, at=sec(0.18), duration=sec(0.02))
    _hold().schedule(px, at=sec(0.2), duration=sec(0.4), source=white)
    return build(beat=1.0, duration=0.8)["test"]


def wave_endpoint() -> bytes:
    s = strip("test", length=1)
    px = s.pixels("0")
    w = wave(channel="V", h=0.0, s=0.0, v=0.0, min_val=0.0, max_val=1.0,
             period=sec(0.4), phase0=PI / 2, pixel_step=0.0)
    w.schedule(px, at=0, duration=sec(0.4))
    _hold().schedule(px, at=sec(0.4), duration=sec(0.4), source=w)
    return build(beat=1.0, duration=0.8)["test"]


def shift_chain() -> bytes:
    s = strip("test", length=4)
    px = s.pixels("0-3")
    ramp = paint(colors=[_gray(0.2), _gray(0.4), _gray(0.6), _gray(0.8)], format="hsv")
    ramp.schedule(px, at=0, duration=sec(0.1))
    mover = shift(direction="right", velocity=10, circular=True, fill="transparent")
    mover.schedule(px, at=sec(0.1), duration=sec(0.2), source=ramp)
    _hold().schedule(s.pixels("1-2"), at=sec(0.3), duration=sec(0.3), source=mover)
    return build(beat=1.0, duration=0.8)["test"]


def self_source() -> bytes:
    s = strip("test", length=1)
    px = s.pixels("0")
    white = paint(colors=[_gray(1.0)], format="hsv")
    white.schedule(px, at=0, duration=sec(0.1))
    _hold().schedule(px, at=sec(0.1), duration=sec(0.2))
    return build(beat=1.0, duration=0.6)["test"]


def slot_reuse() -> bytes:
    s = strip("test", length=1)
    px = s.pixels("0")
    red = paint(colors=[(0.0, 1.0, 1.0)], format="hsv")
    red.schedule(px, at=0, duration=sec(0.1))
    blue = paint(colors=[(240.0, 1.0, 1.0)], format="hsv")
    blue.schedule(px, at=sec(0.1), duration=sec(0.1))
    _hold().schedule(px, at=sec(0.2), duration=sec(0.5), source=red)
    green = paint(colors=[(120.0, 1.0, 1.0)], format="hsv")
    green.schedule(px, at=0, duration=sec(0.3))
    yellow = paint(colors=[(60.0, 1.0, 1.0)], format="hsv")
    yellow.schedule(px, at=sec(0.3), duration=sec(0.1))
    _hold().schedule(px, at=sec(0.4), duration=sec(0.2), source=green)
    return build(beat=1.0, duration=1.0)["test"]


CASES = {
    "skipped_direct": skipped_direct,
    "skipped_copy": skipped_copy,
    "wave_endpoint": wave_endpoint,
    "shift_chain": shift_chain,
    "self_source": self_source,
    "slot_reuse": slot_reuse,
}


def main():
    out_dir = _out_dir()
    for name, make in CASES.items():
        blob = make()
        out = out_dir / f"test_handoff_{name}.bin"
        out.write_bytes(blob)
        print(f"wrote {out} ({len(blob)} bytes)")


if __name__ == "__main__":
    main()
