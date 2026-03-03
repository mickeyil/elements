"""Tests for the Elements v2 compiler.

Runs the test animation (wave+shift+sparks) through the full pipeline
and validates the output blob.
"""

import math
import pytest
import struct

from elements.dsl import *
from elements.blob import decode_blob, ANIM_WAVE, ANIM_SHIFT, ANIM_SPARK
from elements.compiler import CompileError
from pathlib import Path
import re


# ---------------------------------------------------------------------------
# Fixture: build test animation once per module
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def test_blob():
    """The test animation from docs/dsl_example.py, built once."""
    strip1 = strip("main", length=10, type="RGB")
    all_pixels = strip1.pixels("0-9")
    left_group1 = strip1.pixels("0,4")
    right_group1 = strip1.pixels("5,9")

    blue_hue = 220

    wave1 = wave(
        channel="V",
        h=blue_hue, s=1.0,
        min_val=0.0, max_val=0.4,
        period=8,
        phase0=-PI/2,
        pixel_step=PI
    )

    shift1 = shift(
        direction="right",
        velocity=2,
        circular=False,
        fill="transparent"
    )

    spark_white = spark(color="white", fade=0.1)
    spark_yellow = spark(color="yellow", fade=0.1)

    wave1.schedule(all_pixels, at=0, duration=2)
    shift1.schedule(all_pixels, at=2, duration=2, snapshot=wave1)

    for i in range(4):
        spark_white.schedule(left_group1, at=i, duration=sec(0.1))
        spark_yellow.schedule(right_group1, at=i + 0.5, duration=sec(0.1))

    return build(beat=0.5, duration=2.0)


@pytest.fixture(scope="module")
def decoded(test_blob):
    """Decoded blob structure."""
    return decode_blob(test_blob)


# ---------------------------------------------------------------------------
# Header tests
# ---------------------------------------------------------------------------

class TestHeader:
    def test_magic(self, test_blob):
        assert test_blob[:4] == b"ELEM"

    def test_version(self, test_blob):
        assert test_blob[4] == 1

    def test_layer_count(self, decoded):
        assert decoded["header"]["layer_count"] == 2

    def test_buffer_count(self, decoded):
        assert decoded["header"]["buffer_count"] == 1

    def test_duration(self, decoded):
        assert abs(decoded["header"]["duration"] - 2.0) < 1e-6


# ---------------------------------------------------------------------------
# Layer structure tests
# ---------------------------------------------------------------------------

class TestLayers:
    def test_layer0_index_map(self, decoded):
        assert decoded["layers"][0]["index_map"] == list(range(10))

    def test_layer0_event_count(self, decoded):
        assert len(decoded["layers"][0]["events"]) == 2

    def test_layer0_event_types(self, decoded):
        events = decoded["layers"][0]["events"]
        assert events[0]["anim_type"] == ANIM_WAVE
        assert events[1]["anim_type"] == ANIM_SHIFT

    def test_layer1_index_map(self, decoded):
        """Layer 1 should have merged index map from both spark groups."""
        assert decoded["layers"][1]["index_map"] == [0, 4, 5, 9]

    def test_layer1_event_count(self, decoded):
        """8 spark events: 4 white + 4 yellow, interleaved."""
        assert len(decoded["layers"][1]["events"]) == 8

    def test_layer1_all_sparks(self, decoded):
        for e in decoded["layers"][1]["events"]:
            assert e["anim_type"] == ANIM_SPARK


# ---------------------------------------------------------------------------
# Remap tests
# ---------------------------------------------------------------------------

class TestRemap:
    def test_wave_remap_identity(self, decoded):
        wave_evt = decoded["layers"][0]["events"][0]
        assert wave_evt["remap"] == list(range(10))
        assert wave_evt["remap_is_identity"] is True

    def test_shift_remap_identity(self, decoded):
        shift_evt = decoded["layers"][0]["events"][1]
        assert shift_evt["remap"] == list(range(10))
        assert shift_evt["remap_is_identity"] is True

    def test_spark_white_remap(self, decoded):
        """spark_white targets [0,4] → remap [0,1] in merged layer [0,4,5,9]."""
        spark_events = decoded["layers"][1]["events"]
        white_events = [e for e in spark_events
                        if abs(e["params"]["color_h"] - 0.0) < 1e-6]
        for e in white_events:
            assert e["remap"] == [0, 1]
            assert e["remap_is_identity"] is False

    def test_spark_yellow_remap(self, decoded):
        """spark_yellow targets [5,9] → remap [2,3] in merged layer [0,4,5,9]."""
        spark_events = decoded["layers"][1]["events"]
        yellow_events = [e for e in spark_events
                         if abs(e["params"]["color_h"] - 60.0) < 1e-6]
        for e in yellow_events:
            assert e["remap"] == [2, 3]
            assert e["remap_is_identity"] is False


# ---------------------------------------------------------------------------
# Time resolution tests
# ---------------------------------------------------------------------------

class TestTimeResolution:
    def test_wave_timing(self, decoded):
        wave_evt = decoded["layers"][0]["events"][0]
        assert abs(wave_evt["t_start"] - 0.0) < 1e-6
        assert abs(wave_evt["duration"] - 1.0) < 1e-6

    def test_shift_timing(self, decoded):
        shift_evt = decoded["layers"][0]["events"][1]
        assert abs(shift_evt["t_start"] - 1.0) < 1e-6
        assert abs(shift_evt["duration"] - 1.0) < 1e-6

    def test_spark_white_first_timing(self, decoded):
        """First white spark: at=0 beats → 0.0s, duration=sec(0.1) → 0.1s."""
        spark_events = decoded["layers"][1]["events"]
        first = min(spark_events, key=lambda e: e["t_start"])
        assert abs(first["t_start"] - 0.0) < 1e-6
        assert abs(first["duration"] - 0.1) < 1e-6

    def test_wave_period_converted(self, decoded):
        """Wave period=8 beats → 4.0 seconds."""
        wave_evt = decoded["layers"][0]["events"][0]
        assert abs(wave_evt["params"]["period"] - 4.0) < 1e-6

    def test_spark_fade_converted(self, decoded):
        """Spark fade=0.1 beats → 0.05 seconds."""
        spark_evt = decoded["layers"][1]["events"][0]
        assert abs(spark_evt["params"]["fade"] - 0.05) < 1e-6

    def test_shift_velocity_converted(self, decoded):
        """Shift velocity=2 pixels/beat → 4.0 pixels/sec (2 / 0.5)."""
        shift_evt = decoded["layers"][0]["events"][1]
        assert abs(shift_evt["params"]["velocity"] - 4.0) < 1e-6


# ---------------------------------------------------------------------------
# Shift / buffer tests
# ---------------------------------------------------------------------------

class TestShiftAndBuffers:
    def test_shift_buffer_id(self, decoded):
        shift_evt = decoded["layers"][0]["events"][1]
        assert shift_evt["params"]["buffer_id"] == 0

    def test_shift_init_mode_snapshot(self, decoded):
        shift_evt = decoded["layers"][0]["events"][1]
        assert shift_evt["params"]["init_mode"] == 1  # SNAPSHOT

    def test_shift_source_layer(self, decoded):
        """Shift snapshots wave, which is on layer 0."""
        shift_evt = decoded["layers"][0]["events"][1]
        assert shift_evt["params"]["source_layer"] == 0

    def test_buffer_pool_size(self, decoded):
        assert decoded["buffer_pool"] == [10]  # 10 pixels for shift


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------

class TestValidation:
    def test_pixel_out_of_bounds(self):
        strip1 = strip("test_oob", length=10, type="RGB")
        bad_pixels = strip1.pixels("0,11")
        w = wave(channel="V", h=0, s=1.0, min_val=0.0, max_val=1.0,
                 period=4, phase0=0, pixel_step=0)
        w.schedule(bad_pixels, at=0, duration=1)
        with pytest.raises(CompileError, match="out of bounds"):
            build(beat=0.5, duration=2.0)

    def test_event_starts_after_duration(self):
        strip1 = strip("test_dur", length=10, type="RGB")
        px = strip1.pixels("0-9")
        w = wave(channel="V", h=0, s=1.0, min_val=0.0, max_val=1.0,
                 period=4, phase0=0, pixel_step=0)
        w.schedule(px, at=0, duration=1)
        w2 = wave(channel="V", h=0, s=1.0, min_val=0.0, max_val=1.0,
                  period=4, phase0=0, pixel_step=0)
        w2.schedule(px, at=10, duration=1)  # at=10 beats = 5.0s > 2.0s
        with pytest.raises(CompileError, match="starts at"):
            build(beat=0.5, duration=2.0)

    def test_missing_required_param(self):
        strip1 = strip("test_param", length=10, type="RGB")
        px = strip1.pixels("0-9")
        w = wave(channel="V", h=0, s=1.0)  # missing period, phase0, etc
        w.schedule(px, at=0, duration=1)
        with pytest.raises(CompileError, match="missing required param"):
            build(beat=0.5, duration=2.0)


def _extract_int(pattern: str, text: str, description: str) -> int:
    m = re.search(pattern, text)
    if not m:
        raise AssertionError(f"could not find {description}")
    return int(m.group(1))


def test_layer_limit_contract():
    repo_root = Path(__file__).resolve().parents[2]
    compiler_path = repo_root / "compiler" / "elements" / "compiler.py"
    compositor_path = repo_root / "src" / "compositor.h"
    decoder_path = repo_root / "src" / "decoder.cpp"

    compiler_src = compiler_path.read_text(encoding="utf-8")
    compositor_src = compositor_path.read_text(encoding="utf-8")
    decoder_src = decoder_path.read_text(encoding="utf-8")

    compiler_limit = _extract_int(
        r"def _infer_layers\(.*max_layers:\s*int\s*=\s*(\d+)\)",
        compiler_src,
        "_infer_layers default max_layers",
    )
    decoder_limit = _extract_int(
        r"if\s*\(layer_count\s*>\s*(\d+)\)",
        decoder_src,
        "decoder layer_count upper bound",
    )
    compositor_limit = _extract_int(
        r"#define\s+MAX_LAYERS\s+(\d+)",
        compositor_src,
        "compositor MAX_LAYERS",
    )

    assert compiler_limit == decoder_limit == compositor_limit == 32
