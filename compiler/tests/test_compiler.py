"""Tests for the Elements v2 compiler.

Runs the test animation (wave+shift+sparks) through the full pipeline
and validates the output blob.
"""

import math
import pytest
import struct

from elements.dsl import *
from elements.blob import decode_blob, ANIM_WAVE, ANIM_SHIFT, ANIM_SPARK, ANIM_PAINT
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
        h=blue_hue, s=1.0, v=0.0,
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
    shift1.schedule(all_pixels, at=2, duration=2, source=wave1)

    for i in range(4):
        spark_white.schedule(left_group1, at=i, duration=sec(0.1))
        spark_yellow.schedule(right_group1, at=i + 0.5, duration=sec(0.1))

    blobs = build(beat=0.5, duration=2.0)
    return blobs["main"]


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
        assert test_blob[4] == 2

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

    def test_shift_source_layer(self, decoded):
        """Shift sources wave, which is on layer 0."""
        shift_evt = decoded["layers"][0]["events"][1]
        assert shift_evt["source_layer"] == 0

    def test_source_none_for_non_source_events(self, decoded):
        wave_evt = decoded["layers"][0]["events"][0]
        assert wave_evt["source_layer"] == 0xFF

    def test_buffer_pool_size(self, decoded):
        assert decoded["buffer_pool"] == [10]  # 10 pixels for shift


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------

class TestValidation:
    def test_negative_beat_rejected(self):
        strip1 = strip("test_beat", length=10, type="RGB")
        px = strip1.pixels("0-9")
        w = wave(channel="V", h=0, s=1.0, v=0.0, min_val=0.0, max_val=1.0,
                 period=4, phase0=0, pixel_step=0)
        w.schedule(px, at=0, duration=1)
        with pytest.raises(CompileError, match="beat must be > 0"):
            build(beat=-0.5, duration=2.0)

    def test_nonfinite_beat_rejected(self):
        strip1 = strip("test_beat_nonfinite", length=10, type="RGB")
        px = strip1.pixels("0-9")
        w = wave(channel="V", h=0, s=1.0, v=0.0, min_val=0.0, max_val=1.0,
                 period=4, phase0=0, pixel_step=0)
        w.schedule(px, at=0, duration=1)
        with pytest.raises(CompileError, match="beat must be finite"):
            build(beat=math.nan, duration=2.0)

    def test_zero_or_negative_duration_rejected(self):
        strip1 = strip("test_dur", length=10, type="RGB")
        px = strip1.pixels("0-9")
        w = wave(channel="V", h=0, s=1.0, v=0.0, min_val=0.0, max_val=1.0,
                 period=4, phase0=0, pixel_step=0)
        w.schedule(px, at=0, duration=1)
        with pytest.raises(CompileError, match="program duration must be > 0"):
            build(beat=0.5, duration=0)

    def test_nonfinite_program_duration_rejected(self):
        strip1 = strip("test_dur_nonfinite", length=10, type="RGB")
        px = strip1.pixels("0-9")
        w = wave(channel="V", h=0, s=1.0, v=0.0, min_val=0.0, max_val=1.0,
                 period=4, phase0=0, pixel_step=0)
        w.schedule(px, at=0, duration=1)
        with pytest.raises(CompileError, match="program duration must be finite"):
            build(beat=0.5, duration=math.inf)

    def test_negative_event_start_rejected(self):
        strip1 = strip("test_neg_start", length=10, type="RGB")
        px = strip1.pixels("0-9")
        w = wave(channel="V", h=0, s=1.0, v=0.0, min_val=0.0, max_val=1.0,
                 period=4, phase0=0, pixel_step=0)
        w.schedule(px, at=-1, duration=1)
        with pytest.raises(CompileError, match="event start time must be"):
            build(beat=0.5, duration=2.0)

    def test_zero_event_duration_rejected(self):
        strip1 = strip("test_zero_dur", length=10, type="RGB")
        px = strip1.pixels("0-9")
        w = wave(channel="V", h=0, s=1.0, v=0.0, min_val=0.0, max_val=1.0,
                 period=4, phase0=0, pixel_step=0)
        w.schedule(px, at=0, duration=0)
        with pytest.raises(CompileError, match="event duration must be > 0"):
            build(beat=0.5, duration=2.0)

    def test_nan_event_times_rejected(self):
        strip1 = strip("test_nan", length=10, type="RGB")
        px = strip1.pixels("0-9")
        w = wave(channel="V", h=0, s=1.0, v=0.0, min_val=0.0, max_val=1.0,
                 period=4, phase0=0, pixel_step=0)
        w.schedule(px, at=sec(math.nan), duration=1)
        with pytest.raises(CompileError, match="event start time must be"):
            build(beat=0.5, duration=2.0)

    def test_nan_event_duration_rejected(self):
        strip1 = strip("test_nan_duration", length=10, type="RGB")
        px = strip1.pixels("0-9")
        w = wave(channel="V", h=0, s=1.0, v=0.0, min_val=0.0, max_val=1.0,
                 period=4, phase0=0, pixel_step=0)
        w.schedule(px, at=0, duration=sec(math.nan))
        with pytest.raises(CompileError, match="event duration"):
            build(beat=0.5, duration=2.0)

    def test_pixel_out_of_bounds(self):
        strip1 = strip("test_oob", length=10, type="RGB")
        bad_pixels = strip1.pixels("0,11")
        w = wave(channel="V", h=0, s=1.0, v=0.0, min_val=0.0, max_val=1.0,
                 period=4, phase0=0, pixel_step=0)
        w.schedule(bad_pixels, at=0, duration=1)
        with pytest.raises(CompileError, match="out of bounds"):
            build(beat=0.5, duration=2.0)

    def test_event_starts_after_duration(self):
        strip1 = strip("test_dur", length=10, type="RGB")
        px = strip1.pixels("0-9")
        w = wave(channel="V", h=0, s=1.0, v=0.0, min_val=0.0, max_val=1.0,
                 period=4, phase0=0, pixel_step=0)
        w.schedule(px, at=0, duration=1)
        w2 = wave(channel="V", h=0, s=1.0, v=0.0, min_val=0.0, max_val=1.0,
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

    def test_snapshot_key_rejected(self):
        strip1 = strip("test_snapshot", length=10)
        px = strip1.pixels("0-9")
        w = wave(channel="V", h=0, s=1.0, v=0.0, min_val=0.0, max_val=1.0,
                 period=4, phase0=0, pixel_step=0)
        s = shift(direction="right", velocity=2, circular=False, fill="transparent")
        s.schedule(px, at=0, duration=1, snapshot=w)
        with pytest.raises(CompileError, match="snapshot is not supported"):
            build(beat=0.5, duration=2.0)

    def test_source_compiles_correctly(self):
        strip1 = strip("test_source_ok", length=10)
        px = strip1.pixels("0-9")
        source = wave(
            channel="V",
            h=0, s=1.0, v=0.0,
            min_val=0.0, max_val=0.4,
            period=4, phase0=0, pixel_step=0,
        )
        sh = shift(direction="right", velocity=2, circular=False, fill="transparent")
        source.schedule(px, at=0, duration=2)
        sh.schedule(px, at=2, duration=2, source=source)
        blobs = build(beat=0.5, duration=3.0)
        dec = decode_blob(blobs["test_source_ok"])
        assert dec["layers"][0]["events"][1]["source_layer"] == 0

    def test_source_missing_event_error(self):
        strip1 = strip("test_source_missing", length=10)
        px = strip1.pixels("0-9")
        w = wave(channel="V", h=0, s=1.0, v=0.0, min_val=0.0, max_val=1.0,
                 period=4, phase0=0, pixel_step=0)
        s = shift(direction="right", velocity=2, circular=False, fill="transparent")
        s.schedule(px, at=0, duration=1, source=w)
        with pytest.raises(CompileError, match="has no scheduled events"):
            build(beat=0.5, duration=2.0)

    def test_source_ambiguous_anim_error(self):
        strip1 = strip("test_source_ambiguous", length=10)
        px = strip1.pixels("0-9")
        source = wave(
            channel="V",
            h=0, s=1.0, v=0.0,
            min_val=0.0, max_val=1.0,
            period=4, phase0=0, pixel_step=0,
        )
        sh = shift(direction="right", velocity=2, circular=False, fill="transparent")
        source.schedule(strip1.pixels("0-4"), at=0, duration=3)
        source.schedule(strip1.pixels("5-9"), at=1, duration=3)
        sh.schedule(px, at=7, duration=1, source=source)
        with pytest.raises(CompileError, match="appears on multiple layers"):
            build(beat=0.5, duration=5.0)

    def test_source_higher_layer_error(self):
        strip1 = strip("test_source_order", length=10)
        px = strip1.pixels("0-9")
        anchor = wave(
            channel="V",
            h=60, s=1.0, v=0.0,
            min_val=0.0, max_val=1.0,
            period=4, phase0=0, pixel_step=0,
        )
        source = wave(
            channel="V",
            h=120, s=1.0, v=0.0,
            min_val=0.0, max_val=1.0,
            period=4, phase0=0, pixel_step=0,
        )
        sh = shift(direction="right", velocity=2, circular=False, fill="transparent")
        anchor.schedule(px, at=0, duration=4)
        # Force source onto layer 1 by overlapping with anchor on the same pixels
        source.schedule(px, at=0.5, duration=3)
        # Source is on a higher layer than shift
        sh.schedule(px, at=4.5, duration=1, source=source)
        with pytest.raises(CompileError, match="must be <= dependent layer"):
            build(beat=0.5, duration=6.0)


# ---------------------------------------------------------------------------
# Multi-strip tests
# ---------------------------------------------------------------------------

class TestMultiStrip:
    def _make_wave(self, **extra):
        params = dict(channel="V", h=220, s=1.0, v=0.0, min_val=0.0, max_val=1.0,
                      period=4, phase0=0.0, pixel_step=0.1)
        params.update(extra)
        return wave(**params)

    def test_two_strips_produce_two_blobs(self):
        strip_a = strip("left", length=10)
        strip_b = strip("right", length=10)
        w1 = self._make_wave()
        w2 = self._make_wave(h=120)
        w1.schedule(strip_a.pixels("0-9"), at=0, duration=2)
        w2.schedule(strip_b.pixels("0-9"), at=0, duration=2)
        blobs = build(beat=0.5, duration=1.0)
        assert set(blobs.keys()) == {"left", "right"}
        assert blobs["left"][:4] == b"ELEM"
        assert blobs["right"][:4] == b"ELEM"

    def test_strips_have_independent_layers(self):
        """Events on strip_a don't affect layer count on strip_b."""
        strip_a = strip("ind_a", length=10)
        strip_b = strip("ind_b", length=10)
        w1 = self._make_wave()
        w2 = self._make_wave(h=120)
        sp = spark(color="white", fade=0.1)
        w1.schedule(strip_a.pixels("0-9"), at=0, duration=2)
        w2.schedule(strip_b.pixels("0-9"), at=0, duration=2)
        # Overlapping spark on strip_a forces a second layer on strip_a only
        sp.schedule(strip_a.pixels("0,2,4"), at=0, duration=sec(0.1))
        blobs = build(beat=0.5, duration=1.0)
        dec_a = decode_blob(blobs["ind_a"])
        dec_b = decode_blob(blobs["ind_b"])
        assert dec_a["header"]["layer_count"] == 2
        assert dec_b["header"]["layer_count"] == 1

    def test_overlapping_indices_different_strips_not_merged(self):
        """Simultaneous events on different strips with same indices get separate blobs."""
        strip_a = strip("oi_a", length=5)
        strip_b = strip("oi_b", length=5)
        w1 = self._make_wave()
        w2 = self._make_wave(h=120)
        # Same pixel indices [0-4], same time window — different strips
        w1.schedule(strip_a.pixels("0-4"), at=0, duration=2)
        w2.schedule(strip_b.pixels("0-4"), at=0, duration=2)
        blobs = build(beat=0.5, duration=1.0)
        assert set(blobs.keys()) == {"oi_a", "oi_b"}
        # Each strip gets exactly 1 layer — no cross-strip merging
        assert decode_blob(blobs["oi_a"])["header"]["layer_count"] == 1
        assert decode_blob(blobs["oi_b"])["header"]["layer_count"] == 1

    def test_single_strip_still_works(self):
        """Single-strip programs return a dict with one entry."""
        strip1 = strip("solo", length=5)
        w = self._make_wave()
        w.schedule(strip1.pixels("0-4"), at=0, duration=2)
        blobs = build(beat=0.5, duration=1.0)
        assert list(blobs.keys()) == ["solo"]
        assert blobs["solo"][:4] == b"ELEM"


def _extract_int(pattern: str, text: str, description: str) -> int:
    m = re.search(pattern, text)
    if not m:
        raise AssertionError(f"could not find {description}")
    return int(m.group(1))


# ---------------------------------------------------------------------------
# Paint tests
# ---------------------------------------------------------------------------

class TestPaint:
    def _build_paint(self, **kw):
        """Helper: build a single paint event and decode it."""
        strip1 = strip("p", length=3)
        px = strip1.pixels("0-2")
        p = paint(**kw)
        p.schedule(px, at=0, duration=1)
        blobs = build(beat=1.0, duration=1.0)
        return decode_blob(blobs["p"])

    def test_paint_solid_compiles(self):
        dec = self._build_paint(color="red")
        evt = dec["layers"][0]["events"][0]
        assert evt["anim_type"] == ANIM_PAINT
        p = evt["params"]
        assert p["mode"] == 0
        assert abs(p["color_h"] - 0.0) < 1e-6
        assert abs(p["color_s"] - 1.0) < 1e-6
        assert abs(p["color_v"] - 1.0) < 1e-6
        assert abs(p["color_a"] - 1.0) < 1e-6

    def test_paint_per_pixel_compiles(self):
        dec = self._build_paint(colors=[(0, 1.0, 1.0), (120, 1.0, 0.5), (240, 0.5, 0.8)])
        evt = dec["layers"][0]["events"][0]
        p = evt["params"]
        assert p["mode"] == 1
        assert p["pixel_count"] == 3
        assert abs(p["pixels"][0]["h"] - 0.0) < 1e-6
        assert abs(p["pixels"][1]["h"] - 120.0) < 1e-6
        assert abs(p["pixels"][2]["v"] - 0.8) < 1e-6

    def test_paint_alpha_default(self):
        dec = self._build_paint(color=(60, 1.0, 1.0))
        p = dec["layers"][0]["events"][0]["params"]
        assert abs(p["color_a"] - 1.0) < 1e-6

    def test_paint_missing_both_error(self):
        strip1 = strip("pm", length=3)
        px = strip1.pixels("0-2")
        p = paint()
        p.schedule(px, at=0, duration=1)
        with pytest.raises(CompileError, match="requires either"):
            build(beat=1.0, duration=1.0)

    def test_paint_both_error(self):
        strip1 = strip("pb", length=3)
        px = strip1.pixels("0-2")
        p = paint(color="red", colors=[(0, 1, 1), (0, 1, 1), (0, 1, 1)])
        p.schedule(px, at=0, duration=1)
        with pytest.raises(CompileError, match="cannot have both"):
            build(beat=1.0, duration=1.0)

    def test_paint_wrong_pixel_count(self):
        strip1 = strip("pw", length=3)
        px = strip1.pixels("0-2")
        p = paint(colors=[(0, 1, 1)])  # 1 color, 3 pixels
        p.schedule(px, at=0, duration=1)
        with pytest.raises(CompileError, match="length 1 != pixel group size 3"):
            build(beat=1.0, duration=1.0)

    def test_paint_rgb_format(self):
        dec = self._build_paint(color=(255, 0, 0), format="rgb")
        p = dec["layers"][0]["events"][0]["params"]
        assert abs(p["color_h"] - 0.0) < 1e-6
        assert abs(p["color_s"] - 1.0) < 1e-6
        assert abs(p["color_v"] - 1.0) < 1e-6

    def test_paint_invalid_format(self):
        strip1 = strip("pf", length=3)
        px = strip1.pixels("0-2")
        p = paint(color="red", format="xyz")
        p.schedule(px, at=0, duration=1)
        with pytest.raises(CompileError, match="format must be"):
            build(beat=1.0, duration=1.0)

    def test_paint_per_pixel_alpha(self):
        dec = self._build_paint(colors=[(0, 1, 1, 0.5), (120, 1, 0.5, 0.8), (240, 0.5, 0.8, 0.3)])
        p = dec["layers"][0]["events"][0]["params"]
        assert abs(p["pixels"][0]["a"] - 0.5) < 1e-6
        assert abs(p["pixels"][1]["a"] - 0.8) < 1e-6
        assert abs(p["pixels"][2]["a"] - 0.3) < 1e-6

    def test_paint_rgb_per_pixel(self):
        dec = self._build_paint(colors=[(255, 0, 0), (0, 255, 0), (0, 0, 255)], format="rgb")
        p = dec["layers"][0]["events"][0]["params"]
        # Red: H=0, S=1, V=1
        assert abs(p["pixels"][0]["h"] - 0.0) < 1e-6
        assert abs(p["pixels"][0]["s"] - 1.0) < 1e-6
        # Green: H=120, S=1, V=1
        assert abs(p["pixels"][1]["h"] - 120.0) < 1e-6
        # Blue: H=240, S=1, V=1
        assert abs(p["pixels"][2]["h"] - 240.0) < 1e-6


# ---------------------------------------------------------------------------
# Safe interval analysis tests
# ---------------------------------------------------------------------------

class TestSafeIntervals:
    """Tests for dependency-aware safe interval analysis."""

    def _wave(self, **extra):
        params = dict(channel="V", h=0, s=1.0, v=0.0,
                      min_val=0.0, max_val=1.0, period=4, phase0=0, pixel_step=0)
        params.update(extra)
        return wave(**params)

    def test_single_event_full_coverage(self):
        """Single event [0, duration) → only degenerate (0,0)."""
        s = strip("si_full", length=5)
        w = self._wave()
        w.schedule(s.pixels("0-4"), at=0, duration=4)
        m = build_manifest(beat=1.0, duration=4.0)
        assert m.safe_intervals == [(0.0, 0.0)]

    def test_gap_between_events(self):
        """Two unrelated events with a real gap."""
        s = strip("si_gap", length=5)
        w1 = self._wave()
        w2 = self._wave(h=60)
        w1.schedule(s.pixels("0-4"), at=0, duration=1)   # [0, 1)
        w2.schedule(s.pixels("0-4"), at=3, duration=1)   # [3, 4)
        m = build_manifest(beat=1.0, duration=5.0)
        assert m.safe_intervals == [(0.0, 0.0), (1.0, 3.0), (4.0, 5.0)]

    def test_source_dependent_removes_gap(self):
        """Source-dependent event makes the apparent gap unsafe."""
        s = strip("si_dep", length=5)
        p = paint(colors=[(0, 0, 0.2)] * 5)
        sh = shift(direction="right", velocity=1, circular=False, fill="transparent")
        p.schedule(s.pixels("0-4"), at=0, duration=1)             # [0, 1)
        sh.schedule(s.pixels("0-4"), at=sec(2.0), duration=sec(2.0), source=p)  # [2, 4)
        m = build_manifest(beat=1.0, duration=5.0)
        # Shift required_start_sec = paint required_start_sec = 0.0
        # Merged unsafe: [0, 4). Safe: (0,0) + [4, 5)
        assert m.safe_intervals == [(0.0, 0.0), (4.0, 5.0)]

    def test_transitive_source_chain(self):
        """A→B→C: C's required_start propagates back through B to A."""
        s = strip("si_trans", length=5)
        px = s.pixels("0-4")
        # A: paint [0, 1)
        a = paint(colors=[(0, 0, 0.2)] * 5)
        a.schedule(px, at=0, duration=1)
        # B: shift(source=A) [2, 3)  — required_start = A.required_start = 0.0
        b = shift(direction="right", velocity=1, circular=False, fill="transparent")
        b.schedule(px, at=sec(2.0), duration=sec(1.0), source=a)
        # C: shift(source=B) [4, 5)  — required_start = B.required_start = 0.0
        c = shift(direction="right", velocity=1, circular=False, fill="transparent")
        c.schedule(px, at=sec(4.0), duration=sec(1.0), source=b)
        m = build_manifest(beat=1.0, duration=6.0)
        # All three chain back to t=0. Merged unsafe: [0, 5).
        # Naive algorithm would say [1,2) and [3,4) are safe — wrong.
        assert m.safe_intervals == [(0.0, 0.0), (5.0, 6.0)]

    def test_overlapping_unsafe_spans_merge(self):
        """Overlapping unsafe spans merge — no fragmented false gaps."""
        s = strip("si_merge", length=5)
        w1 = self._wave()
        w2 = self._wave(h=60)
        sp = spark(color="white", fade=sec(0.5))
        # Layer 0: wave [0, 3)
        w1.schedule(s.pixels("0-4"), at=0, duration=3)
        # Layer 1 (forced by overlap): wave [1, 4)
        w2.schedule(s.pixels("0-4"), at=1, duration=3)
        # Layer 1 also gets spark [0, 0.5) — non-overlapping with w2
        sp.schedule(s.pixels("0-4"), at=0, duration=sec(0.5))
        m = build_manifest(beat=1.0, duration=5.0)
        # All events cover [0, 4). Safe: (0,0) + [4, 5)
        assert m.safe_intervals == [(0.0, 0.0), (4.0, 5.0)]

    def test_multi_strip_global_intersection(self):
        """Safe intervals are the intersection across strips."""
        sa = strip("si_ms_a", length=5)
        sb = strip("si_ms_b", length=5)
        wa = self._wave()
        wb = self._wave(h=60)
        wa.schedule(sa.pixels("0-4"), at=0, duration=1)  # strip A: unsafe [0,1)
        wb.schedule(sb.pixels("0-4"), at=2, duration=1)  # strip B: unsafe [2,3)
        m = build_manifest(beat=1.0, duration=4.0)
        # Strip A per-strip safe: (0,0), [1, 4)
        # Strip B per-strip safe: [0, 2), [3, 4)
        # Intersection: (0,0), [1,2), [3,4)
        assert m.safe_intervals == [(0.0, 0.0), (1.0, 2.0), (3.0, 4.0)]

    def test_event_clamped_at_duration(self):
        """Event extending past duration uses clamped end for safe intervals."""
        s = strip("si_clamp", length=5)
        w = self._wave()
        w.schedule(s.pixels("0-4"), at=0, duration=10)  # extends way past duration
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m = build_manifest(beat=1.0, duration=3.0)
        # Clamped to [0, 3) → full coverage
        assert m.safe_intervals == [(0.0, 0.0)]

    def test_adjacent_events_no_gap(self):
        """Adjacent events [0,2) and [2,4) → no gap, only degenerate."""
        s = strip("si_adj", length=5)
        w1 = self._wave()
        w2 = self._wave(h=60)
        w1.schedule(s.pixels("0-4"), at=0, duration=2)
        w2.schedule(s.pixels("0-4"), at=2, duration=2)
        m = build_manifest(beat=1.0, duration=4.0)
        assert m.safe_intervals == [(0.0, 0.0)]

    def test_manifest_strip_order_follows_declaration(self):
        """Manifest strips follow the input strips list order, not event order."""
        # Declare strips in order: c, a, b
        sc = strip("si_ord_c", length=5)
        sa = strip("si_ord_a", length=5)
        sb = strip("si_ord_b", length=5)
        # Schedule events in reverse order: b, a, c
        wb = self._wave(h=120)
        wa = self._wave(h=60)
        wc = self._wave()
        wb.schedule(sb.pixels("0-4"), at=0, duration=1)
        wa.schedule(sa.pixels("0-4"), at=0, duration=1)
        wc.schedule(sc.pixels("0-4"), at=0, duration=1)
        m = build_manifest(beat=1.0, duration=2.0)
        assert [s.strip_id for s in m.strips] == ["si_ord_c", "si_ord_a", "si_ord_b"]

    def test_manifest_omits_eventless_strips(self):
        """Strips with no events are omitted from the manifest."""
        sa = strip("si_has", length=5)
        strip("si_empty", length=5)  # declared but no events
        w = self._wave()
        w.schedule(sa.pixels("0-4"), at=0, duration=1)
        m = build_manifest(beat=1.0, duration=2.0)
        assert len(m.strips) == 1
        assert m.strips[0].strip_id == "si_has"

    def test_backwards_compatibility(self):
        """compile_program() and build() still return dict[str, bytes]."""
        s = strip("si_compat", length=5)
        w = self._wave()
        w.schedule(s.pixels("0-4"), at=0, duration=1)
        blobs = build(beat=1.0, duration=2.0)
        assert isinstance(blobs, dict)
        assert isinstance(blobs["si_compat"], bytes)
        assert blobs["si_compat"][:4] == b"ELEM"
        assert set(blobs.keys()) == {"si_compat"}


def test_layer_limit_contract():
    """Compiler max_layers and decoder layer_count limit must agree (32)."""
    repo_root = Path(__file__).resolve().parents[2]
    compiler_path = repo_root / "compiler" / "elements" / "compiler.py"
    decoder_path = repo_root / "src" / "decoder.cpp"

    compiler_src = compiler_path.read_text(encoding="utf-8")
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

    # Compositor uses uint32_t active_mask, implicitly limiting to 32 layers
    assert compiler_limit == decoder_limit == 32
