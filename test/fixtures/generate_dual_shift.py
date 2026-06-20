#!/usr/bin/env python3
"""Generate dual-strip fixture for ESPSimulated integration tests.

Two strips of length 5 with identical timing but distinct grayscale payloads:

  left strip:  paint [51, 102, 153, 204, 255] for 1s, shift right 1px/s for 4s
  right strip: paint [255, 204, 153, 102, 51] for 1s, shift right 1px/s for 4s

Expected RGB at key times (no gamma, S=0 → R=G=B=round(V*255)):

  left  t=1.0: [51, 102, 153, 204, 255]    right t=1.0: [255, 204, 153, 102, 51]
  left  t=2.0: [0,  51,  102, 153, 204]    right t=2.0: [0,   255, 204, 153, 102]
  left  t=3.0: [0,  0,   51,  102, 153]    right t=3.0: [0,   0,   255, 204, 153]
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
    left = strip("left", length=5)
    right = strip("right", length=5)

    lpx = left.pixels("0-4")
    rpx = right.pixels("0-4")

    # Left strip: ascending brightness
    lp = paint(
        colors=[(0, 0, 0.2), (0, 0, 0.4), (0, 0, 0.6), (0, 0, 0.8), (0, 0, 1.0)],
        format="hsv",
    )
    lp.schedule(lpx, at=0, duration=1)

    lsh = shift(direction="right", velocity=1, circular=False, fill="transparent")
    lsh.schedule(lpx, at=sec(1.0), duration=sec(4.0), source=lp)

    # Right strip: descending brightness
    rp = paint(
        colors=[(0, 0, 1.0), (0, 0, 0.8), (0, 0, 0.6), (0, 0, 0.4), (0, 0, 0.2)],
        format="hsv",
    )
    rp.schedule(rpx, at=0, duration=1)

    rsh = shift(direction="right", velocity=1, circular=False, fill="transparent")
    rsh.schedule(rpx, at=sec(1.0), duration=sec(4.0), source=rp)

    blobs = build(beat=1.0, duration=5.0)

    fixtures = _out_dir()
    left_path = fixtures / "test_dual_shift_left.bin"
    right_path = fixtures / "test_dual_shift_right.bin"

    left_path.write_bytes(blobs["left"])
    right_path.write_bytes(blobs["right"])

    print(f"wrote {left_path} ({len(blobs['left'])} bytes)")
    print(f"wrote {right_path} ({len(blobs['right'])} bytes)")


if __name__ == "__main__":
    main()
