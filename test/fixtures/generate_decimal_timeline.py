#!/usr/bin/env python3
"""Generate test_decimal_timeline.bin: boundaries that float32 could not place.

Beat is 0.3 s and the program is 0.9 s, so no boundary is a binary
fraction. On 4 pixels, one layer:
  - Paint A, grayscale V=0.4, beats [0, 1)      -> [0, 300) ms
  - Paint B, grayscale V=0.8, beats [1, 2)      -> [300, 600) ms, adjacent
  - Shift with velocity 0, sec(0.6) for sec(0.3) -> [600, 900) ms, source=A

B overwrites A's buffer, so the compiler preserves A with a copy op at
A's end (300 ms), the same instant B starts.

Expected RGB (S=0 -> R=G=B=round(V*255)):
  t=0, 299:   [102, 102, 102, 102]   paint A
  t=300, 599: [204, 204, 204, 204]   paint B
  t=600, 899: [102, 102, 102, 102]   shift replays A's snapshot
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
    s = strip("test", length=4)
    px = s.pixels("0-3")

    a = paint(colors=[(0, 0, 0.4)] * 4, format="hsv")
    a.schedule(px, at=0, duration=1)

    b = paint(colors=[(0, 0, 0.8)] * 4, format="hsv")
    b.schedule(px, at=1, duration=1)

    sh = shift(direction="right", velocity=0, circular=True, fill="transparent")
    sh.schedule(px, at=sec(0.6), duration=sec(0.3), source=a)

    blobs = build(beat=0.3, duration=0.9)

    out = _out_dir() / "test_decimal_timeline.bin"
    out.write_bytes(blobs["test"])
    print(f"wrote {out} ({len(blobs['test'])} bytes)")

if __name__ == "__main__":
    main()
