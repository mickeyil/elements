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


# ---------------------------------------------------------------------------
# Helper: run the test animation from dsl_example.py
# ---------------------------------------------------------------------------

def build_test_animation():
    """The test animation from drafts/dsl_example.py."""
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

    return compile(beat=0.5, duration=2.0)


# ---------------------------------------------------------------------------
# Header tests
# ---------------------------------------------------------------------------

class TestHeader:
    def test_magic(self):
        blob = build_test_animation()
        assert blob[:4] == b"ELEM"

    def test_version(self):
        blob = build_test_animation()
        assert blob[4] == 1

    def test_layer_count(self):
        blob = build_test_animation()
        decoded = decode_blob(blob)
        assert decoded["header"]["layer_count"] == 2

    def test_buffer_count(self):
        blob = build_test_animation()
        decoded = decode_blob(blob)
        assert decoded["header"]["buffer_count"] == 1

    def test_duration(self):
        blob = build_test_animation()
        decoded = decode_blob(blob)
        assert abs(decoded["header"]["duration"] - 2.0) < 1e-6


# ---------------------------------------------------------------------------
# Layer structure tests
# ---------------------------------------------------------------------------

class TestLayers:
    def test_layer0_index_map(self):
        blob = build_test_animation()
        decoded = decode_blob(blob)
        assert decoded["layers"][0]["index_map"] == list(range(10))

    def test_layer0_event_count(self):
        blob = build_test_animation()
        decoded = decode_blob(blob)
        assert len(decoded["layers"][0]["events"]) == 2

    def test_layer0_event_types(self):
        blob = build_test_animation()
        decoded = decode_blob(blob)
        events = decoded["layers"][0]["events"]
        assert events[0]["anim_type"] == ANIM_WAVE
        assert events[1]["anim_type"] == ANIM_SHIFT

    def test_layer1_index_map(self):
        """Layer 1 should have merged index map from both spark groups."""
        blob = build_test_animation()
        decoded = decode_blob(blob)
        assert decoded["layers"][1]["index_map"] == [0, 4, 5, 9]

    def test_layer1_event_count(self):
        """8 spark events: 4 white + 4 yellow, interleaved."""
        blob = build_test_animation()
        decoded = decode_blob(blob)
        assert len(decoded["layers"][1]["events"]) == 8

    def test_layer1_all_sparks(self):
        blob = build_test_animation()
        decoded = decode_blob(blob)
        for e in decoded["layers"][1]["events"]:
            assert e["anim_type"] == ANIM_SPARK


# ---------------------------------------------------------------------------
# Remap tests
# ---------------------------------------------------------------------------

class TestRemap:
    def test_wave_remap_identity(self):
        blob = build_test_animation()
        decoded = decode_blob(blob)
        wave_evt = decoded["layers"][0]["events"][0]
        assert wave_evt["remap"] == list(range(10))

    def test_shift_remap_identity(self):
        blob = build_test_animation()
        decoded = decode_blob(blob)
        shift_evt = decoded["layers"][0]["events"][1]
        assert shift_evt["remap"] == list(range(10))

    def test_spark_white_remap(self):
        """spark_white targets [0,4] → remap [0,1] in merged layer [0,4,5,9]."""
        blob = build_test_animation()
        decoded = decode_blob(blob)
        spark_events = decoded["layers"][1]["events"]
        # White sparks are at times 0.0, 0.5, 1.0, 1.5
        white_events = [e for e in spark_events
                        if abs(e["params"]["color_h"] - 0.0) < 1e-6]
        for e in white_events:
            assert e["remap"] == [0, 1]

    def test_spark_yellow_remap(self):
        """spark_yellow targets [5,9] → remap [2,3] in merged layer [0,4,5,9]."""
        blob = build_test_animation()
        decoded = decode_blob(blob)
        spark_events = decoded["layers"][1]["events"]
        # Yellow sparks have H=60
        yellow_events = [e for e in spark_events
                         if abs(e["params"]["color_h"] - 60.0) < 1e-6]
        for e in yellow_events:
            assert e["remap"] == [2, 3]


# ---------------------------------------------------------------------------
# Time resolution tests
# ---------------------------------------------------------------------------

class TestTimeResolution:
    def test_wave_timing(self):
        blob = build_test_animation()
        decoded = decode_blob(blob)
        wave_evt = decoded["layers"][0]["events"][0]
        assert abs(wave_evt["t_start"] - 0.0) < 1e-6
        assert abs(wave_evt["duration"] - 1.0) < 1e-6

    def test_shift_timing(self):
        blob = build_test_animation()
        decoded = decode_blob(blob)
        shift_evt = decoded["layers"][0]["events"][1]
        assert abs(shift_evt["t_start"] - 1.0) < 1e-6
        assert abs(shift_evt["duration"] - 1.0) < 1e-6

    def test_spark_white_first_timing(self):
        """First white spark: at=0 beats → 0.0s, duration=sec(0.1) → 0.1s."""
        blob = build_test_animation()
        decoded = decode_blob(blob)
        spark_events = decoded["layers"][1]["events"]
        # Find first spark at t=0
        first = min(spark_events, key=lambda e: e["t_start"])
        assert abs(first["t_start"] - 0.0) < 1e-6
        assert abs(first["duration"] - 0.1) < 1e-6

    def test_wave_period_converted(self):
        """Wave period=8 beats → 4.0 seconds."""
        blob = build_test_animation()
        decoded = decode_blob(blob)
        wave_evt = decoded["layers"][0]["events"][0]
        assert abs(wave_evt["params"]["period"] - 4.0) < 1e-6

    def test_spark_fade_converted(self):
        """Spark fade=0.1 beats → 0.05 seconds."""
        blob = build_test_animation()
        decoded = decode_blob(blob)
        spark_evt = decoded["layers"][1]["events"][0]
        assert abs(spark_evt["params"]["fade"] - 0.05) < 1e-6

    def test_shift_velocity_converted(self):
        """Shift velocity=2 pixels/beat → 4.0 pixels/sec (2 / 0.5)."""
        blob = build_test_animation()
        decoded = decode_blob(blob)
        shift_evt = decoded["layers"][0]["events"][1]
        assert abs(shift_evt["params"]["velocity"] - 4.0) < 1e-6


# ---------------------------------------------------------------------------
# Shift / buffer tests
# ---------------------------------------------------------------------------

class TestShiftAndBuffers:
    def test_shift_buffer_id(self):
        blob = build_test_animation()
        decoded = decode_blob(blob)
        shift_evt = decoded["layers"][0]["events"][1]
        assert shift_evt["params"]["buffer_id"] == 0

    def test_shift_init_mode_snapshot(self):
        blob = build_test_animation()
        decoded = decode_blob(blob)
        shift_evt = decoded["layers"][0]["events"][1]
        assert shift_evt["params"]["init_mode"] == 1  # SNAPSHOT

    def test_shift_source_layer(self):
        """Shift snapshots wave, which is on layer 0."""
        blob = build_test_animation()
        decoded = decode_blob(blob)
        shift_evt = decoded["layers"][0]["events"][1]
        assert shift_evt["params"]["source_layer"] == 0

    def test_buffer_pool_size(self):
        blob = build_test_animation()
        decoded = decode_blob(blob)
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
            compile(beat=0.5, duration=2.0)

    def test_event_starts_after_duration(self):
        strip1 = strip("test_dur", length=10, type="RGB")
        px = strip1.pixels("0-9")
        w = wave(channel="V", h=0, s=1.0, min_val=0.0, max_val=1.0,
                 period=4, phase0=0, pixel_step=0)
        w.schedule(px, at=0, duration=1)  # valid event
        w2 = wave(channel="V", h=0, s=1.0, min_val=0.0, max_val=1.0,
                  period=4, phase0=0, pixel_step=0)
        w2.schedule(px, at=10, duration=1)  # at=10 beats = 5.0s > 2.0s
        with pytest.raises(CompileError, match="starts at"):
            compile(beat=0.5, duration=2.0)
