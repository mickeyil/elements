"""Whether a device should be offered the built firmware image.

Versions are the strings tools/firmware_version.py stamps: `M.N` for a
clean build, `M.N+d` for one made from uncommitted firmware sources, or
`unknown` when the build could not tell. Devices report theirs in
DISCOVER ('' for firmware that predates reporting it); sims report a
commit hash and never take an update.

"Available" means the update would leave the device running something it
does not run now, as far as the two strings can show:

  1. A dirty image is always offered: its label does not pin its content,
     so it may differ from anything the device runs, and a developer
     flashing work in progress wants the button.
  2. Otherwise, a device whose version is missing, dirty, or not a clean
     `M.N` is offered any image: what it runs cannot be matched against
     anything, so the operator decides.
  3. Otherwise, an image whose version is missing or not a clean `M.N`
     (no sidecar, or a build that said `unknown`) is offered for the same
     reason.
  4. Otherwise both are clean, and the image is offered only when its
     (major, minor) is strictly greater: an equal version is the same
     commit range, and an older one is a downgrade the operator did not
     ask for.

No image on disk means nothing to offer, whatever the versions say.
Pure functions; the service applies them per device and the web UI only
reads the result.
"""

from __future__ import annotations

DIRTY_SUFFIX = '+d'


def parse_version(s):
    """(major, minor) for a clean `M.N` of plain decimal integers, else None.

    Dirty (`+d`), hash-style, `unknown`, empty and None all parse to None;
    callers tell dirty apart with is_dirty()."""
    if not isinstance(s, str):
        return None
    parts = s.split('.')
    if len(parts) != 2 or not all(p.isascii() and p.isdigit() for p in parts):
        return None
    return int(parts[0]), int(parts[1])


def is_dirty(s):
    """True when s is a version built from uncommitted sources."""
    return isinstance(s, str) and s.endswith(DIRTY_SUFFIX)


def update_available(device_version, available_version, image_present):
    """Whether flashing the image would plausibly change what the device runs.

    device_version is what the device last reported (None if never heard,
    '' if it predates reporting); available_version is the image's sidecar
    version (None if it has none). Sims are the caller's to exclude: their
    uid, not their version, is what marks them."""
    if not image_present:
        return False
    if is_dirty(available_version):
        return True
    device = parse_version(device_version)
    image = parse_version(available_version)
    if device is None or image is None:
        return True
    return image > device
