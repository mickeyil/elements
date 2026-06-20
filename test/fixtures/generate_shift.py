#!/usr/bin/env python3
"""Generate test_shift.bin fixture for PlaybackDevice end-to-end tests.

Simple paint→shift program on 5 pixels:
  - Paint 5 pixels with V=0.2, 0.4, 0.6, 0.8, 1.0 (grayscale) for 1 second
  - Shift right at 1 pixel/sec, non-circular, fill transparent, for 4 seconds

Expected RGB (no gamma, S=0 → R=G=B=round(V*255)):
  t=1.0: [51, 102, 153, 204, 255]
  t=2.0: [0,  51,  102, 153, 204]
  t=3.0: [0,  0,   51,  102, 153]
  t=4.0: [0,  0,   0,   51,  102]
"""

import sys
from pathlib import Path
from elements.dsl import *


def _out_dir() -> Path:
    """Output directory: argv[1] if given (CMake passes the build dir), else here."""
    d = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
    d.mkdir(parents=True, exist_ok=True)
    return d

def main():
    s = strip("test", length=5)
    px = s.pixels("0-4")

    # Paint 5 pixels with distinct brightness values (H=0, S=0, V=...)
    p = paint(
        colors=[(0, 0, 0.2), (0, 0, 0.4), (0, 0, 0.6), (0, 0, 0.8), (0, 0, 1.0)],
        format="hsv",
    )
    p.schedule(px, at=0, duration=1)

    # Shift right at 1 pixel/beat (= 1 pixel/sec with beat=1.0)
    sh = shift(direction="right", velocity=1, circular=False, fill="transparent")
    sh.schedule(px, at=sec(1.0), duration=sec(4.0), source=p)

    blobs = build(beat=1.0, duration=5.0)

    out = _out_dir() / "test_shift.bin"
    out.write_bytes(blobs["test"])
    print(f"wrote {out} ({len(blobs['test'])} bytes)")

if __name__ == "__main__":
    main()
