"""Parity between the Python caps mirror and the C++ decoder headers.

If a cap changes in src/, this test fails until elements/limits.py is
updated to match. The compiler relies on these values to reject blobs
the decoder would reject.
"""

import re
from pathlib import Path

from elements import limits

REPO_ROOT = Path(__file__).resolve().parents[2]

_CONSTEXPR = re.compile(
    r"static\s+constexpr\s+\w+\s+(\w+)\s*=\s*([0-9]+(?:\s*\*\s*[0-9]+)?)\s*;"
)


def _parse_constexpr_ints(path: Path) -> dict[str, int]:
    """Extract `static constexpr <type> NAME = <expr>;` integer constants.

    Handles plain decimal literals and a single `A * B` product (the form
    MAX_POOL_BYTES uses).
    """
    out: dict[str, int] = {}
    for name, expr in _CONSTEXPR.findall(path.read_text(encoding="utf-8")):
        value = 1
        for token in expr.split("*"):
            value *= int(token)
        out[name] = value
    return out


def test_blob_limits_match_cpp():
    cpp = _parse_constexpr_ints(REPO_ROOT / "src" / "blob_limits.h")
    cpp.update(_parse_constexpr_ints(REPO_ROOT / "src" / "hardware_profile.h"))

    expected = {
        "MAX_LAYER_COUNT": limits.MAX_LAYER_COUNT,
        "MAX_STRIP_PIXELS": limits.MAX_STRIP_PIXELS,
        "MAX_BUFFER_COUNT": limits.MAX_BUFFER_COUNT,
        "MAX_PIXEL_VIEW_COUNT": limits.MAX_PIXEL_VIEW_COUNT,
        "MAX_COPY_OP_COUNT": limits.MAX_COPY_OP_COUNT,
        "MAX_EVENTS_PER_LAYER": limits.MAX_EVENTS_PER_LAYER,
        "MAX_PARAMS_BYTES": limits.MAX_PARAMS_BYTES,
        "MAX_POOL_BYTES": limits.MAX_POOL_BYTES,
    }

    for name, py_value in expected.items():
        assert name in cpp, f"{name} not found in C++ headers"
        assert cpp[name] == py_value, f"{name}: Python {py_value} != C++ {cpp[name]}"
