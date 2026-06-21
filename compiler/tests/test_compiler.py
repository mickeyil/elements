"""Tests for the Elements v3 compiler.

Runs the test animation (wave+shift+sparks) through the full pipeline and
validates the emitted v3 blob structure, plus the v3-specific analyses:
per-event dst views, work views, source dependency tracing, copy ops, the
safe-interval width filter, and structural caps.
"""

import math
import re
from pathlib import Path

import pytest

from elements.dsl import *
from elements.dsl import _builder
from elements.blob import (
    decode_blob, decode_params, PIXV_NONE,
    ANIM_WAVE, ANIM_SHIFT, ANIM_SPARK, ANIM_PAINT,
)
from elements.compiler import CompileError


# ---------------------------------------------------------------------------
# Fixture: build the canonical test animation once per module
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def test_blob():
    """The canonical wave+shift+sparks animation, built once."""
    strip1 = strip("main", length=10, type="RGB")
    all_pixels = strip1.pixels("0-9")
    left_group1 = strip1.pixels("0,4")
    right_group1 = strip1.pixels("5,9")

    blue_hue = 220

    wave1 = wave(channel="V", h=blue_hue, s=1.0, v=0.0, min_val=0.0, max_val=0.4,
                 period=8, phase0=-PI / 2, pixel_step=PI)
    shift1 = shift(direction="right", velocity=2, circular=False, fill="transparent")
    spark_white = spark(color="white", fade=0.1)
    spark_yellow = spark(color="yellow", fade=0.1)

    wave1.schedule(all_pixels, at=0, duration=2)
    shift1.schedule(all_pixels, at=2, duration=2, source=wave1)
    for i in range(4):
        spark_white.schedule(left_group1, at=i, duration=sec(0.1))
        spark_yellow.schedule(right_group1, at=i + 0.5, duration=sec(0.1))

    return build(beat=0.5, duration=2.0)["main"]


@pytest.fixture(scope="module")
def decoded(test_blob):
    return decode_blob(test_blob)


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

class TestHeader:
    def test_magic(self, test_blob):
        assert test_blob[:4] == b"ELEM"

    def test_version(self, test_blob):
        assert test_blob[4] == 3

    def test_layer_count(self, decoded):
        assert len(decoded.layers) == 2

    def test_duration(self, decoded):
        assert abs(decoded.duration - 2.0) < 1e-6

    def test_program_config_defaults(self, decoded):
        assert decoded.target_fps == 50
        assert decoded.requires_sync is False

    def test_strip_length(self, decoded):
        assert decoded.strip_length == 10


# ---------------------------------------------------------------------------
# Layers / events
# ---------------------------------------------------------------------------

class TestLayers:
    def test_layer0_events(self, decoded):
        events = decoded.layers[0].events
        assert len(events) == 2
        assert events[0].anim_type == ANIM_WAVE
        assert events[1].anim_type == ANIM_SHIFT

    def test_layer1_all_sparks(self, decoded):
        events = decoded.layers[1].events
        assert len(events) == 8
        assert all(e.anim_type == ANIM_SPARK for e in events)


# ---------------------------------------------------------------------------
# Pixel views and physical maps (replaces v2 index_map / remap)
# ---------------------------------------------------------------------------

class TestViews:
    def test_wave_dst_is_full_strip_identity(self, decoded):
        wave_evt = decoded.layers[0].events[0]
        v = decoded.pixel_views[wave_evt.dst_pixv_idx]
        assert v.size == 10
        assert v.has_physical and v.physical_identity
        assert v.physical_indices is None  # identity needs no array

    def test_spark_white_physical_indices(self, decoded):
        for e in decoded.layers[1].events:
            if abs(decode_params(ANIM_SPARK, e.params)["color_h"] - 0.0) < 1e-6:
                v = decoded.pixel_views[e.dst_pixv_idx]
                assert v.has_physical and not v.physical_identity
                assert v.physical_indices == [0, 4]

    def test_spark_yellow_physical_indices(self, decoded):
        for e in decoded.layers[1].events:
            if abs(decode_params(ANIM_SPARK, e.params)["color_h"] - 60.0) < 1e-6:
                v = decoded.pixel_views[e.dst_pixv_idx]
                assert v.physical_indices == [5, 9]

    def test_dst_views_must_have_physical(self, decoded):
        for layer in decoded.layers:
            for e in layer.events:
                assert decoded.pixel_views[e.dst_pixv_idx].has_physical


# ---------------------------------------------------------------------------
# Time resolution
# ---------------------------------------------------------------------------

class TestTimeResolution:
    def test_wave_timing(self, decoded):
        wave_evt = decoded.layers[0].events[0]
        assert abs(wave_evt.start - 0.0) < 1e-6
        assert abs(wave_evt.duration - 1.0) < 1e-6

    def test_shift_timing(self, decoded):
        shift_evt = decoded.layers[0].events[1]
        assert abs(shift_evt.start - 1.0) < 1e-6
        assert abs(shift_evt.duration - 1.0) < 1e-6

    def test_first_spark_timing(self, decoded):
        first = min(decoded.layers[1].events, key=lambda e: e.start)
        assert abs(first.start - 0.0) < 1e-6
        assert abs(first.duration - 0.1) < 1e-6

    def test_wave_period_converted(self, decoded):
        """period=8 beats -> 4.0 seconds."""
        p = decode_params(ANIM_WAVE, decoded.layers[0].events[0].params)
        assert abs(p["period"] - 4.0) < 1e-6

    def test_spark_fade_converted(self, decoded):
        """fade=0.1 beats -> 0.05 seconds."""
        p = decode_params(ANIM_SPARK, decoded.layers[1].events[0].params)
        assert abs(p["fade"] - 0.05) < 1e-6

    def test_shift_velocity_converted(self, decoded):
        """velocity=2 pixels/beat -> 4.0 pixels/sec (2 / 0.5)."""
        p = decode_params(ANIM_SHIFT, decoded.layers[0].events[1].params)
        assert abs(p["velocity"] - 4.0) < 1e-6


# ---------------------------------------------------------------------------
# Shift views and source dependency
# ---------------------------------------------------------------------------

class TestShiftDependency:
    def test_shift_has_work_view(self, decoded):
        shift_evt = decoded.layers[0].events[1]
        assert shift_evt.work_pixv_idx != PIXV_NONE
        work = decoded.pixel_views[shift_evt.work_pixv_idx]
        assert not work.has_physical
        assert work.size == 10

    def test_shift_src_reuses_wave_dst(self, decoded):
        """Whole-source read with an intact source buffer needs no copy op."""
        wave_evt = decoded.layers[0].events[0]
        shift_evt = decoded.layers[0].events[1]
        assert shift_evt.src_pixv_idx == wave_evt.dst_pixv_idx
        assert len(decoded.copy_ops) == 0

    def test_shift_no_buffer_id_in_params(self, decoded):
        # v3 shift params are 22 bytes; the v2 trailing buffer_id byte is gone.
        assert len(decoded.layers[0].events[1].params) == 22


class TestSourceResolution:
    def test_source_compiles(self):
        strip1 = strip("src_ok", length=10)
        px = strip1.pixels("0-9")
        source = wave(channel="V", h=0, s=1.0, v=0.0, min_val=0.0, max_val=0.4,
                      period=4, phase0=0, pixel_step=0)
        sh = shift(direction="right", velocity=2, circular=False, fill="transparent")
        source.schedule(px, at=0, duration=2)
        sh.schedule(px, at=2, duration=2, source=source)
        p = decode_blob(build(beat=0.5, duration=3.0)["src_ok"])
        wave_evt, shift_evt = p.layers[0].events
        assert shift_evt.src_pixv_idx == wave_evt.dst_pixv_idx

    def test_overwritten_source_emits_copy_op(self):
        """A later write to the source buffer forces a preserve copy."""
        s = strip("overwrite", length=5)
        x = paint(color="red")
        y = paint(color="green")
        z = shift(direction="right", velocity=1, circular=False, fill="transparent")
        x.schedule(s.pixels("0-4"), at=0, duration=1)
        y.schedule(s.pixels("0-4"), at=1, duration=1)   # overwrites x's slots
        z.schedule(s.pixels("0-4"), at=2, duration=1, source=x)
        p = decode_blob(build(beat=1.0, duration=4.0)["overwrite"])
        assert len(p.copy_ops) == 1
        assert abs(p.copy_ops[0].at - 1.0) < 1e-6   # at source end
        z_evt = p.layers[0].events[2]
        assert not decoded_view(p, z_evt.src_pixv_idx).has_physical
        assert z_evt.src_pixv_idx != z_evt.dst_pixv_idx

    def test_shift_without_source_reads_own_dst(self):
        s = strip("nosrc", length=5)
        x = paint(color="red")
        z = shift(direction="right", velocity=1, circular=False, fill="transparent")
        x.schedule(s.pixels("0-4"), at=0, duration=1)
        z.schedule(s.pixels("0-4"), at=1, duration=1)   # no source=
        p = decode_blob(build(beat=1.0, duration=3.0)["nosrc"])
        z_evt = p.layers[0].events[1]
        assert z_evt.src_pixv_idx == z_evt.dst_pixv_idx
        assert z_evt.work_pixv_idx != PIXV_NONE
        assert len(p.copy_ops) == 0

    def test_required_start_propagates_through_copy(self):
        """The shift's unsafe span reaches back to the source's start."""
        s = strip("reqstart", length=5)
        x = paint(color="red")
        y = paint(color="green")
        z = shift(direction="right", velocity=1, circular=False, fill="transparent")
        x.schedule(s.pixels("0-4"), at=0, duration=1)
        y.schedule(s.pixels("0-4"), at=1, duration=1)
        z.schedule(s.pixels("0-4"), at=2, duration=1, source=x)
        m = build_manifest(beat=1.0, duration=4.0)
        # x,y,z all chain back to 0 -> unsafe [0,3); safe (0,0) + [3,4).
        assert m.safe_intervals == [(0.0, 0.0), (3.0, 4.0)]

    def test_source_on_non_shift_rejected(self):
        s = strip("src_nonshift", length=5)
        source = wave(channel="V", h=0, s=1.0, v=0.0, min_val=0.0, max_val=1.0,
                      period=4, phase0=0, pixel_step=0)
        p = paint(color="red")
        source.schedule(s.pixels("0-4"), at=0, duration=1)
        p.schedule(s.pixels("0-4"), at=1, duration=1, source=source)
        with pytest.raises(CompileError, match="only supported on shift"):
            build(beat=1.0, duration=3.0)

    def test_source_missing_event_error(self):
        strip1 = strip("src_missing", length=10)
        px = strip1.pixels("0-9")
        w = wave(channel="V", h=0, s=1.0, v=0.0, min_val=0.0, max_val=1.0,
                 period=4, phase0=0, pixel_step=0)
        s = shift(direction="right", velocity=2, circular=False, fill="transparent")
        s.schedule(px, at=0, duration=1, source=w)
        with pytest.raises(CompileError, match="has no scheduled events"):
            build(beat=0.5, duration=2.0)

    def test_source_ambiguous_anim_error(self):
        strip1 = strip("src_ambig", length=10)
        px = strip1.pixels("0-9")
        source = wave(channel="V", h=0, s=1.0, v=0.0, min_val=0.0, max_val=1.0,
                      period=4, phase0=0, pixel_step=0)
        sh = shift(direction="right", velocity=2, circular=False, fill="transparent")
        source.schedule(strip1.pixels("0-4"), at=0, duration=3)
        source.schedule(strip1.pixels("5-9"), at=1, duration=3)
        sh.schedule(px, at=7, duration=1, source=source)
        with pytest.raises(CompileError, match="appears on multiple layers"):
            build(beat=0.5, duration=5.0)

    def test_source_higher_layer_error(self):
        strip1 = strip("src_order", length=10)
        px = strip1.pixels("0-9")
        anchor = wave(channel="V", h=60, s=1.0, v=0.0, min_val=0.0, max_val=1.0,
                      period=4, phase0=0, pixel_step=0)
        source = wave(channel="V", h=120, s=1.0, v=0.0, min_val=0.0, max_val=1.0,
                      period=4, phase0=0, pixel_step=0)
        sh = shift(direction="right", velocity=2, circular=False, fill="transparent")
        anchor.schedule(px, at=0, duration=4)
        source.schedule(px, at=0.5, duration=3)   # forced onto layer 1
        sh.schedule(px, at=4.5, duration=1, source=source)
        with pytest.raises(CompileError, match="must be <= dependent layer"):
            build(beat=0.5, duration=6.0)


def decoded_view(program, idx):
    return program.pixel_views[idx]


# ---------------------------------------------------------------------------
# Validation
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


class TestConfiguredStripLengths:
    def teardown_method(self):
        _builder.reset()

    def test_strip_without_length_requires_context(self):
        with pytest.raises(ValueError, match="length required for strip 'main'"):
            strip("main")

    def test_strip_without_length_uses_configured_context(self):
        _builder.reset()
        _builder.configured_strip_lengths = {"main": 144}
        s = strip("main")
        assert s.length == 144

    def test_explicit_shorter_length_is_allowed_with_context(self):
        _builder.reset()
        _builder.configured_strip_lengths = {"main": 144}
        s = strip("main", length=60)
        assert s.length == 60

    def test_explicit_longer_length_is_rejected_with_context(self):
        _builder.reset()
        _builder.configured_strip_lengths = {"main": 144}
        with pytest.raises(ValueError, match="exceeds configured length 144"):
            strip("main", length=145)

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

    def test_event_starts_at_duration_rejected(self):
        # at == duration would clamp to a zero-duration event the decoder rejects.
        strip1 = strip("test_at_eq_dur", length=10, type="RGB")
        px = strip1.pixels("0-9")
        w = wave(channel="V", h=0, s=1.0, v=0.0, min_val=0.0, max_val=1.0,
                 period=4, phase0=0, pixel_step=0)
        w.schedule(px, at=2, duration=1)  # at=2 beats = 2.0s == duration
        with pytest.raises(CompileError, match="starts at"):
            build(beat=1.0, duration=2.0)

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


# ---------------------------------------------------------------------------
# Structural caps (mirror src/blob_limits.h)
# ---------------------------------------------------------------------------

class TestCaps:
    def test_layer_limit_exceeded(self):
        s = strip("cap_layers", length=5)
        # 33 fully overlapping events -> 33 layers -> over MAX_LAYER_COUNT.
        for i in range(33):
            w = wave(channel="V", h=i, s=1.0, v=0.0, min_val=0.0, max_val=1.0,
                     period=4, phase0=0, pixel_step=0)
            w.schedule(s.pixels("0-4"), at=0, duration=1)
        with pytest.raises(CompileError, match="layer limit"):
            build(beat=1.0, duration=2.0)

    def test_strip_length_cap(self):
        s = strip("cap_strip", length=400)
        w = wave(channel="V", h=0, s=1.0, v=0.0, min_val=0.0, max_val=1.0,
                 period=4, phase0=0, pixel_step=0)
        w.schedule(s.pixels("0-9"), at=0, duration=1)
        with pytest.raises(CompileError, match="MAX_STRIP_PIXELS"):
            build(beat=1.0, duration=2.0)

    def test_zero_length_strip_rejected(self):
        sa = strip("cap_has", length=5)
        strip("cap_zero", length=0)  # declared, eventless, zero length
        w = wave(channel="V", h=0, s=1.0, v=0.0, min_val=0.0, max_val=1.0,
                 period=4, phase0=0, pixel_step=0)
        w.schedule(sa.pixels("0-4"), at=0, duration=1)
        with pytest.raises(CompileError, match="at least 1"):
            build(beat=1.0, duration=2.0)

    def test_per_pixel_paint_full_strip(self):
        # A per-pixel paint covering a full MAX_STRIP_PIXELS strip compiles and
        # decodes; the u16 count and raised MAX_EVENT_PARAMS_BYTES allow it.
        from elements import limits
        n = limits.MAX_STRIP_PIXELS
        s = strip("cap_paint", length=n)
        p = paint(colors=[(float(i % 360), 1.0, 1.0) for i in range(n)])
        p.schedule(s.pixels(f"0-{n - 1}"), at=0, duration=1)
        m = build_manifest(beat=1.0, duration=2.0)
        program = decode_blob(m.strips["cap_paint"].blob)
        params = decode_params(ANIM_PAINT, program.layers[0].events[0].params)
        assert params["mode"] == 1
        assert params["pixel_count"] == n


# ---------------------------------------------------------------------------
# Multi-strip
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
        strip_a = strip("ind_a", length=10)
        strip_b = strip("ind_b", length=10)
        w1 = self._make_wave()
        w2 = self._make_wave(h=120)
        sp = spark(color="white", fade=0.1)
        w1.schedule(strip_a.pixels("0-9"), at=0, duration=2)
        w2.schedule(strip_b.pixels("0-9"), at=0, duration=2)
        sp.schedule(strip_a.pixels("0,2,4"), at=0, duration=sec(0.1))
        blobs = build(beat=0.5, duration=1.0)
        assert len(decode_blob(blobs["ind_a"]).layers) == 2
        assert len(decode_blob(blobs["ind_b"]).layers) == 1

    def test_single_strip_still_works(self):
        strip1 = strip("solo", length=5)
        w = self._make_wave()
        w.schedule(strip1.pixels("0-4"), at=0, duration=2)
        blobs = build(beat=0.5, duration=1.0)
        assert list(blobs.keys()) == ["solo"]
        assert blobs["solo"][:4] == b"ELEM"


# ---------------------------------------------------------------------------
# Spark
# ---------------------------------------------------------------------------

class TestSpark:
    def test_spark_accepts_hsv_tuple_color(self):
        strip1 = strip("spark_tuple", length=1)
        sp = spark(color=(0.0, 0.0, 0.5), fade=sec(0.5))
        sp.schedule(strip1.pixels("0"), at=0, duration=sec(0.5))

        dec = decode_blob(build(beat=1.0, duration=0.5)["spark_tuple"])
        params = decode_params(ANIM_SPARK, dec.layers[0].events[0].params)
        assert abs(params["color_h"] - 0.0) < 1e-6
        assert abs(params["color_s"] - 0.0) < 1e-6
        assert abs(params["color_v"] - 0.5) < 1e-6
        assert abs(params["fade"] - 0.5) < 1e-6


# ---------------------------------------------------------------------------
# Paint
# ---------------------------------------------------------------------------

class TestPaint:
    def _build_paint(self, **kw):
        strip1 = strip("p", length=3)
        px = strip1.pixels("0-2")
        p = paint(**kw)
        p.schedule(px, at=0, duration=1)
        return decode_blob(build(beat=1.0, duration=1.0)["p"])

    def test_paint_solid_compiles(self):
        dec = self._build_paint(color="red")
        evt = dec.layers[0].events[0]
        assert evt.anim_type == ANIM_PAINT
        p = decode_params(ANIM_PAINT, evt.params)
        assert p["mode"] == 0
        assert abs(p["color_h"] - 0.0) < 1e-6
        assert abs(p["color_s"] - 1.0) < 1e-6
        assert abs(p["color_v"] - 1.0) < 1e-6
        assert abs(p["color_a"] - 1.0) < 1e-6

    def test_paint_per_pixel_compiles(self):
        dec = self._build_paint(colors=[(0, 1.0, 1.0), (120, 1.0, 0.5), (240, 0.5, 0.8)])
        p = decode_params(ANIM_PAINT, dec.layers[0].events[0].params)
        assert p["mode"] == 1
        assert p["pixel_count"] == 3
        assert abs(p["pixels"][0]["h"] - 0.0) < 1e-6
        assert abs(p["pixels"][1]["h"] - 120.0) < 1e-6
        assert abs(p["pixels"][2]["v"] - 0.8) < 1e-6

    def test_paint_alpha_default(self):
        dec = self._build_paint(color=(60, 1.0, 1.0))
        p = decode_params(ANIM_PAINT, dec.layers[0].events[0].params)
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
        p = paint(colors=[(0, 1, 1)])
        p.schedule(px, at=0, duration=1)
        with pytest.raises(CompileError, match="length 1 != pixel group size 3"):
            build(beat=1.0, duration=1.0)

    def test_paint_rgb_format(self):
        dec = self._build_paint(color=(255, 0, 0), format="rgb")
        p = decode_params(ANIM_PAINT, dec.layers[0].events[0].params)
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
        dec = self._build_paint(colors=[(0, 1, 1, 0.5), (120, 1, 0.5, 0.25), (240, 0.5, 0.8, 0.75)])
        p = decode_params(ANIM_PAINT, dec.layers[0].events[0].params)
        assert abs(p["pixels"][0]["a"] - 0.5) < 1e-6
        assert abs(p["pixels"][1]["a"] - 0.25) < 1e-6
        assert abs(p["pixels"][2]["a"] - 0.75) < 1e-6

    def test_paint_rgb_per_pixel(self):
        dec = self._build_paint(colors=[(255, 0, 0), (0, 255, 0), (0, 0, 255)], format="rgb")
        p = decode_params(ANIM_PAINT, dec.layers[0].events[0].params)
        assert abs(p["pixels"][0]["h"] - 0.0) < 1e-6
        assert abs(p["pixels"][1]["h"] - 120.0) < 1e-6
        assert abs(p["pixels"][2]["h"] - 240.0) < 1e-6


# ---------------------------------------------------------------------------
# Safe interval analysis
# ---------------------------------------------------------------------------

class TestSafeIntervals:
    def _wave(self, **extra):
        params = dict(channel="V", h=0, s=1.0, v=0.0,
                      min_val=0.0, max_val=1.0, period=4, phase0=0, pixel_step=0)
        params.update(extra)
        return wave(**params)

    def test_single_event_full_coverage(self):
        s = strip("si_full", length=5)
        w = self._wave()
        w.schedule(s.pixels("0-4"), at=0, duration=4)
        m = build_manifest(beat=1.0, duration=4.0)
        assert m.safe_intervals == [(0.0, 0.0)]

    def test_gap_between_events(self):
        s = strip("si_gap", length=5)
        w1 = self._wave()
        w2 = self._wave(h=60)
        w1.schedule(s.pixels("0-4"), at=0, duration=1)
        w2.schedule(s.pixels("0-4"), at=3, duration=1)
        m = build_manifest(beat=1.0, duration=5.0)
        assert m.safe_intervals == [(0.0, 0.0), (1.0, 3.0), (4.0, 5.0)]

    def test_source_dependent_removes_gap(self):
        s = strip("si_dep", length=5)
        p = paint(colors=[(0, 0, 0.2)] * 5)
        sh = shift(direction="right", velocity=1, circular=False, fill="transparent")
        p.schedule(s.pixels("0-4"), at=0, duration=1)
        sh.schedule(s.pixels("0-4"), at=sec(2.0), duration=sec(2.0), source=p)
        m = build_manifest(beat=1.0, duration=5.0)
        assert m.safe_intervals == [(0.0, 0.0), (4.0, 5.0)]

    def test_transitive_source_chain(self):
        s = strip("si_trans", length=5)
        px = s.pixels("0-4")
        a = paint(colors=[(0, 0, 0.2)] * 5)
        a.schedule(px, at=0, duration=1)
        b = shift(direction="right", velocity=1, circular=False, fill="transparent")
        b.schedule(px, at=sec(2.0), duration=sec(1.0), source=a)
        c = shift(direction="right", velocity=1, circular=False, fill="transparent")
        c.schedule(px, at=sec(4.0), duration=sec(1.0), source=b)
        m = build_manifest(beat=1.0, duration=6.0)
        assert m.safe_intervals == [(0.0, 0.0), (5.0, 6.0)]

    def test_overlapping_unsafe_spans_merge(self):
        s = strip("si_merge", length=5)
        w1 = self._wave()
        w2 = self._wave(h=60)
        sp = spark(color="white", fade=sec(0.5))
        w1.schedule(s.pixels("0-4"), at=0, duration=3)
        w2.schedule(s.pixels("0-4"), at=1, duration=3)
        sp.schedule(s.pixels("0-4"), at=0, duration=sec(0.5))
        m = build_manifest(beat=1.0, duration=5.0)
        assert m.safe_intervals == [(0.0, 0.0), (4.0, 5.0)]

    def test_multi_strip_global_intersection(self):
        sa = strip("si_ms_a", length=5)
        sb = strip("si_ms_b", length=5)
        wa = self._wave()
        wb = self._wave(h=60)
        wa.schedule(sa.pixels("0-4"), at=0, duration=1)
        wb.schedule(sb.pixels("0-4"), at=2, duration=1)
        m = build_manifest(beat=1.0, duration=4.0)
        assert m.safe_intervals == [(0.0, 0.0), (1.0, 2.0), (3.0, 4.0)]

    def test_event_clamped_at_duration(self):
        s = strip("si_clamp", length=5)
        w = self._wave()
        w.schedule(s.pixels("0-4"), at=0, duration=10)
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            m = build_manifest(beat=1.0, duration=3.0)
        assert m.safe_intervals == [(0.0, 0.0)]

    def test_adjacent_events_no_gap(self):
        s = strip("si_adj", length=5)
        w1 = self._wave()
        w2 = self._wave(h=60)
        w1.schedule(s.pixels("0-4"), at=0, duration=2)
        w2.schedule(s.pixels("0-4"), at=2, duration=2)
        m = build_manifest(beat=1.0, duration=4.0)
        assert m.safe_intervals == [(0.0, 0.0)]

    def test_subframe_gap_dropped_by_width_filter(self):
        """A safe gap narrower than one frame period is dropped."""
        s = strip("si_width", length=5)
        w1 = self._wave()
        w2 = self._wave(h=60)
        w1.schedule(s.pixels("0-4"), at=0, duration=1)
        w2.schedule(s.pixels("0-4"), at=1.01, duration=0.99)  # gap (1.0, 1.01) = 0.01s
        m = build_manifest(beat=1.0, duration=2.0, target_fps=50)  # frame = 0.02s
        assert m.safe_intervals == [(0.0, 0.0)]

    def test_manifest_strip_order_follows_declaration(self):
        sc = strip("si_ord_c", length=5)
        sa = strip("si_ord_a", length=5)
        sb = strip("si_ord_b", length=5)
        wb = self._wave(h=120)
        wa = self._wave(h=60)
        wc = self._wave()
        wb.schedule(sb.pixels("0-4"), at=0, duration=1)
        wa.schedule(sa.pixels("0-4"), at=0, duration=1)
        wc.schedule(sc.pixels("0-4"), at=0, duration=1)
        m = build_manifest(beat=1.0, duration=2.0)
        assert list(m.strips) == ["si_ord_c", "si_ord_a", "si_ord_b"]

    def test_manifest_includes_eventless_strips(self):
        sa = strip("si_has", length=5)
        strip("si_empty", length=5)
        w = self._wave()
        w.schedule(sa.pixels("0-4"), at=0, duration=1)
        m = build_manifest(beat=1.0, duration=2.0)
        assert list(m.strips) == ["si_has", "si_empty"]
        assert len(m.strips["si_empty"].blob) > 0

    def test_backwards_compatibility(self):
        s = strip("si_compat", length=5)
        w = self._wave()
        w.schedule(s.pixels("0-4"), at=0, duration=1)
        blobs = build(beat=1.0, duration=2.0)
        assert isinstance(blobs, dict)
        assert isinstance(blobs["si_compat"], bytes)
        assert blobs["si_compat"][:4] == b"ELEM"
        assert set(blobs.keys()) == {"si_compat"}


# ---------------------------------------------------------------------------
# Program config
# ---------------------------------------------------------------------------

class TestProgramConfig:
    def _wave(self, **extra):
        params = dict(channel="V", h=0, s=1.0, v=0.0,
                      min_val=0.0, max_val=1.0, period=4, phase0=0, pixel_step=0)
        params.update(extra)
        return wave(**params)

    def _schedule_one(self, name):
        s = strip(name, length=5)
        self._wave().schedule(s.pixels("0-4"), at=0, duration=1)

    def test_defaults(self):
        self._schedule_one("cfg_default")
        m = build_manifest(beat=1.0, duration=2.0)
        assert m.target_fps == 50
        assert m.requires_sync is False

    def test_values_propagate_to_manifest_and_blob(self):
        self._schedule_one("cfg_custom")
        m = build_manifest(beat=1.0, duration=2.0, target_fps=30, requires_sync=True)
        assert m.target_fps == 30 and m.requires_sync is True
        p = decode_blob(m.strips["cfg_custom"].blob)
        assert p.target_fps == 30 and p.requires_sync is True

    def test_target_fps_out_of_range_rejected(self):
        self._schedule_one("cfg_fps_range")
        with pytest.raises(CompileError, match="target_fps must be in"):
            build(beat=1.0, duration=2.0, target_fps=256)

    def test_target_fps_zero_rejected(self):
        self._schedule_one("cfg_fps_zero")
        with pytest.raises(CompileError, match="target_fps must be in"):
            build(beat=1.0, duration=2.0, target_fps=0)

    def test_target_fps_non_int_rejected(self):
        self._schedule_one("cfg_fps_float")
        with pytest.raises(CompileError, match="target_fps must be an integer"):
            build(beat=1.0, duration=2.0, target_fps=50.0)

    def test_requires_sync_non_bool_rejected(self):
        self._schedule_one("cfg_sync_type")
        with pytest.raises(CompileError, match="requires_sync must be a bool"):
            build(beat=1.0, duration=2.0, requires_sync=1)


def _extract_int(pattern: str, text: str, description: str) -> int:
    m = re.search(pattern, text)
    if not m:
        raise AssertionError(f"could not find {description}")
    return int(m.group(1))


def test_layer_limit_contract():
    """Compiler max_layers must agree with the mirrored MAX_LAYER_COUNT cap.

    The cap's equality with the C++ decoder is pinned by
    test_blob_limits.py; here we only check the compiler's own default
    tracks it.
    """
    from elements import limits

    repo_root = Path(__file__).resolve().parents[2]
    compiler_src = (repo_root / "compiler" / "elements" / "compiler.py").read_text(
        encoding="utf-8"
    )

    compiler_limit = _extract_int(
        r"def _infer_layers\(.*max_layers:\s*int\s*=\s*(\d+)\)",
        compiler_src,
        "_infer_layers default max_layers",
    )

    # Compositor uses uint32_t active_mask, implicitly limiting to 32 layers
    assert compiler_limit == limits.MAX_LAYER_COUNT == 32
