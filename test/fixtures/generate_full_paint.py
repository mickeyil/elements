#!/usr/bin/env python3
"""Generate test_full_paint.bin: a per-pixel paint across a full strip.

Proves the v3 emitter and device decoder handle a per-pixel paint with more
than 255 colors (the count field is a u16). One paint event spanning a full
MAX_STRIP_PIXELS strip, distinct hue per pixel, for 1 second.

Expected RGB at pixel 0: hue 0, S=1, V=1 -> red (255, 0, 0).
"""

import sys
from pathlib import Path
from elements.dsl import *
from elements.limits import MAX_STRIP_PIXELS


def _out_dir() -> Path:
    """Output directory: argv[1] if given (CMake passes the build dir), else here."""
    d = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
    d.mkdir(parents=True, exist_ok=True)
    return d


def main():
    n = MAX_STRIP_PIXELS
    s = strip("full", length=n)
    colors = [(float(i % 360), 1.0, 1.0) for i in range(n)]
    p = paint(colors=colors, format="hsv")
    p.schedule(s.pixels(f"0-{n - 1}"), at=0, duration=1)

    blob = build(beat=1.0, duration=2.0)["full"]
    out = _out_dir() / "test_full_paint.bin"
    out.write_bytes(blob)
    print(f"wrote {out} ({len(blob)} bytes)")


if __name__ == "__main__":
    main()
