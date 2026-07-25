#!/usr/bin/env python3
"""Generate test_pacifica.bin: one full-strip pacifica event."""

import sys
from pathlib import Path
from elements.dsl import *


def _out_dir() -> Path:
    """Output directory: argv[1] if given (CMake passes the build dir), else here."""
    d = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent
    d.mkdir(parents=True, exist_ok=True)
    return d


def main():
    strip1 = strip("main", length=10, type="RGB")
    ocean = pacifica(speed=1.0, brightness=1.0, hue_shift=0.0)
    ocean.schedule(strip1.pixels("0-9"), at=0, duration=sec(4.0))

    blobs = build(beat=1.0, duration=4.0)

    out = _out_dir() / "test_pacifica.bin"
    out.write_bytes(blobs["main"])
    print(f"wrote {out} ({len(blobs['main'])} bytes)")


if __name__ == "__main__":
    main()
