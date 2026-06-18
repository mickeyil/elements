"""Memory-estimate correctness, and parity with real esp32 struct sizes.

The estimate models device RAM with fixed 32-bit constants. test_*_match_esp32
compiles a probe with the esp32 toolchain and asserts those constants still
match the live sizeof; it skips where the toolchain is unavailable.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from elements.dsl import *
from elements.blob_v3 import decode_blob
from elements.memory_estimate import estimate_memory, format_report
from elements import memory_estimate as me

REPO_ROOT = Path(__file__).resolve().parents[2]


def _wave(**extra):
    params = dict(channel="V", h=0, s=1.0, v=0.0, min_val=0.0, max_val=1.0,
                  period=4, phase0=0, pixel_step=0)
    params.update(extra)
    return wave(**params)


# ---------------------------------------------------------------------------
# Estimate correctness
# ---------------------------------------------------------------------------

class TestEstimate:
    def test_pool_bytes_and_total(self):
        s = strip("m", length=10)
        _wave().schedule(s.pixels("0-9"), at=0, duration=1)
        m = build_manifest(beat=1.0, duration=2.0)
        est = m.strips["m"].memory
        program = decode_blob(m.strips["m"].blob)
        nbuf = len(program.buffer_sizes)
        # Pixel storage plus the pool's per-buffer pointer (4) and size (2) tables.
        assert est.pool_bytes == sum(program.buffer_sizes) * 16 + nbuf * 6
        assert est.total_bytes == (est.pool_bytes + est.view_bytes
                                   + est.copy_op_bytes + est.event_bytes
                                   + est.anim_bytes + est.overhead_bytes)
        assert est.target == "esp32"
        assert est.overhead_bytes > 0
        assert est.total_bytes > 0

    def test_peak_is_max_over_strips(self):
        sa = strip("big", length=100)
        sb = strip("small", length=5)
        _wave().schedule(sa.pixels("0-99"), at=0, duration=1)
        _wave(h=60).schedule(sb.pixels("0-4"), at=0, duration=1)
        m = build_manifest(beat=1.0, duration=2.0)
        totals = [a.memory.total_bytes for a in m.strips.values()]
        assert m.peak_memory_bytes == max(totals)
        assert m.strips["big"].memory.pool_bytes > m.strips["small"].memory.pool_bytes

    def test_per_pixel_paint_adds_constant_array(self):
        s_solid = strip("solid", length=8)
        paint(color="red").schedule(s_solid.pixels("0-7"), at=0, duration=1)
        solid = build_manifest(beat=1.0, duration=2.0).strips["solid"].memory

        s_pp = strip("pp", length=8)
        paint(colors=[(0, 1.0, 1.0)] * 8).schedule(s_pp.pixels("0-7"), at=0, duration=1)
        pp = build_manifest(beat=1.0, duration=2.0).strips["pp"].memory

        # Per-pixel mode bakes 8 hsva_t (8 * 16) into the Paint instance.
        assert pp.anim_bytes == solid.anim_bytes + 8 * 16

    def test_eventless_strip_estimate_is_minimal(self):
        sa = strip("has", length=5)
        strip("empty", length=5)
        _wave().schedule(sa.pixels("0-4"), at=0, duration=1)
        m = build_manifest(beat=1.0, duration=2.0)
        empty = m.strips["empty"].memory
        # No pixels, views, or events; only the fixed decoded Program shell.
        assert empty.pool_bytes == 0 and empty.anim_bytes == 0
        assert empty.total_bytes == empty.overhead_bytes > 0

    def test_default_artifact_has_zero_estimate(self):
        from elements.types import CompiledStripArtifact
        a = CompiledStripArtifact(strip_id="x", length=5, blob=b"")
        assert a.memory.total_bytes == 0

    def test_format_report_names_peak(self):
        sa = strip("big", length=100)
        sb = strip("small", length=5)
        _wave().schedule(sa.pixels("0-99"), at=0, duration=1)
        _wave(h=60).schedule(sb.pixels("0-4"), at=0, duration=1)
        m = build_manifest(beat=1.0, duration=2.0)
        report = format_report({sid: a.memory for sid, a in m.strips.items()})
        assert "peak : big" in report
        assert "pool" in report


# ---------------------------------------------------------------------------
# Parity with real esp32 sizeof (skipped without the toolchain)
# ---------------------------------------------------------------------------

def _find_xtensa_gpp() -> Path | None:
    """Locate the esp32 g++ wherever PlatformIO installed it (any version)."""
    on_path = shutil.which("xtensa-esp32-elf-g++")
    if on_path:
        return Path(on_path)
    packages = Path(os.path.expanduser("~/.platformio/packages"))
    # Match the binary name exactly so esp32s2/s3 toolchains don't get picked.
    found = sorted(packages.glob("toolchain-xtensa-esp32*/bin/xtensa-esp32-elf-g++"))
    return found[-1] if found else None


_XTENSA_GPP = _find_xtensa_gpp()

_PROBE = """
#include "program.h"
#include "pixel_view.h"
#include "animations/wave.h"
#include "animations/shift.h"
#include "animations/spark.h"
#include "animations/paint.h"
template <int N> struct ShowSize;
ShowSize<(int)sizeof(hsva_t)>         s_hsva;
ShowSize<(int)sizeof(PixelView)>      s_pixelview;
ShowSize<(int)sizeof(CopyOp)>         s_copyop;
ShowSize<(int)sizeof(AnimationEvent)> s_event;
ShowSize<(int)sizeof(Layer)>          s_layer;
ShowSize<(int)sizeof(Program)>        s_program;
ShowSize<(int)sizeof(void*)>          s_ptr;
ShowSize<(int)sizeof(Wave)>           s_wave;
ShowSize<(int)sizeof(Shift)>          s_shift;
ShowSize<(int)sizeof(Spark)>          s_spark;
ShowSize<(int)sizeof(Paint)>          s_paint;
"""


def _probe_esp32_sizes(tmp_path) -> dict[str, int]:
    probe = tmp_path / "size_probe.cpp"
    probe.write_text(_PROBE)
    result = subprocess.run(
        # -DARDUINO matches the firmware build (PixelBufferPool layout differs).
        [str(_XTENSA_GPP), "-std=gnu++17", "-DARDUINO=1",
         "-I", str(REPO_ROOT / "src"), "-fsyntax-only", str(probe)],
        capture_output=True, text=True,
    )
    sizes = {}
    for name, n in re.findall(
            r"ShowSize<(\d+)> (s_\w+)", result.stderr):
        sizes[n] = int(name)
    return sizes


@pytest.mark.skipif(_XTENSA_GPP is None,
                    reason="esp32 toolchain (xtensa-esp32-elf-g++) not found")
def test_constants_match_esp32_sizeof(tmp_path):
    sizes = _probe_esp32_sizes(tmp_path)
    assert sizes, "could not read sizes from the esp32 probe"
    assert sizes["s_hsva"] == me._HSVA_BYTES
    assert sizes["s_pixelview"] == me._PIXEL_VIEW_BYTES
    assert sizes["s_copyop"] == me._COPY_OP_BYTES
    assert sizes["s_event"] == me._ANIM_EVENT_BYTES
    assert sizes["s_layer"] == me._LAYER_BYTES
    assert sizes["s_program"] == me._PROGRAM_BYTES
    assert sizes["s_ptr"] == me._PTR_BYTES
    assert sizes["s_wave"] == me._ANIM_INSTANCE_BYTES[0]
    assert sizes["s_shift"] == me._ANIM_INSTANCE_BYTES[1]
    assert sizes["s_spark"] == me._ANIM_INSTANCE_BYTES[2]
    assert sizes["s_paint"] == me._ANIM_INSTANCE_BYTES[3]
