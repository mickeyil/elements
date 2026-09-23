#!/usr/bin/env python3
"""Firmware version string for the ESP32 build: `<major>.<minor>[+d]`.

  major  the integer in src/platform/esp32/FIRMWARE_MAJOR, read from the
         working tree. Bumping it is a deliberate, committed act.
  minor  commits that touch the firmware sources since the commit that
         last changed the major file (the anchor). The range is
         anchor..HEAD, which excludes the anchor itself, so the bump
         commit builds as `N.0`. Before the major file is first
         committed there is no anchor and every firmware commit counts.
  +d     the firmware sources or the major file have uncommitted
         changes: the image is not reproducible from any commit.

Only commits inside FIRMWARE_PATHSPEC move the minor, so controller, web
UI, sim or docs work does not suggest a firmware update that changes
nothing on the device.

Used by tools/pio_version.py at build time; run directly to print the
version the next build would get. Outside a git checkout (or without
git) the version is `unknown`, never an exception: a build must not fail
over its label.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Relative to the repo root.
MAJOR_FILE = 'src/platform/esp32/FIRMWARE_MAJOR'

# What the esp32dev image is built from. The sim platform is excluded:
# it shares src/platform but never reaches the device.
FIRMWARE_PATHSPEC = (
    'src/core',
    'src/controller',
    'src/platform',
    ':(exclude)src/platform/sim',
    'platformio.ini',
)

UNKNOWN_VERSION = 'unknown'
DIRTY_SUFFIX = '+d'


class _GitUnavailable(Exception):
    """git is missing, or repo_root is not a usable checkout."""


def _git(repo_root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ['git', '-C', str(repo_root), *args],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, OSError) as e:
        raise _GitUnavailable(str(e)) from e
    return proc.stdout.strip()


def _read_major(repo_root: Path) -> int | None:
    """The working-tree major, or None when missing or not a plain integer."""
    try:
        text = (repo_root / MAJOR_FILE).read_text(encoding='ascii').strip()
    except (OSError, UnicodeDecodeError):
        return None
    return int(text) if text.isdigit() else None


def firmware_version(repo_root: Path | str = REPO_ROOT) -> str:
    """The version string for a firmware built from repo_root right now."""
    repo_root = Path(repo_root)
    major = _read_major(repo_root)
    if major is None:
        return UNKNOWN_VERSION
    try:
        anchor = _git(repo_root, 'log', '-1', '--format=%H', '--', MAJOR_FILE)
        rev_range = f'{anchor}..HEAD' if anchor else 'HEAD'
        minor = int(_git(repo_root, 'rev-list', '--count', rev_range,
                         '--', *FIRMWARE_PATHSPEC))
        dirty = _git(repo_root, 'status', '--porcelain',
                     '--', *FIRMWARE_PATHSPEC, MAJOR_FILE)
    except (_GitUnavailable, ValueError):
        return UNKNOWN_VERSION
    return f'{major}.{minor}{DIRTY_SUFFIX if dirty else ""}'


def main() -> int:
    print(firmware_version(Path(sys.argv[1]) if len(sys.argv) > 1 else REPO_ROOT))
    return 0


if __name__ == '__main__':
    sys.exit(main())
