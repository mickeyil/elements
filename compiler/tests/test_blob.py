"""Round-trip and byte-layout tests for the blob emitter.

Verifies the serializer against docs/blob_format.md (offsets, flags,
section order) and against its own decoder. Byte-for-byte agreement with
src/core/decoder.cpp is checked separately once C++ fixtures regenerate.
"""

import struct

import pytest

from elements.blob import (
    BlobProgram, BlobLayer, BlobEvent, PixelViewSpec, CopyOpSpec,
    emit_blob, decode_blob, pack_params, decode_params,
    BLOB_MAGIC, BLOB_VERSION, PIXV_NONE,
    ANIM_WAVE, ANIM_SHIFT, ANIM_SPARK, ANIM_PAINT,
)


def _sample_program() -> BlobProgram:
    """A program exercising every section, with f32-exact float values."""
    wave_params = pack_params("wave", {
        "channel": 2, "h": 220.0, "s": 1.0, "v": 0.0,
        "min_val": 0.0, "max_val": 0.5,
        "period": 4.0, "phase0": 0.0, "pixel_step": 0.5,
    })
    shift_params = pack_params("shift", {
        "direction": 1, "velocity": 4.0, "circular": 0,
        "fill_h": 0.0, "fill_s": 0.0, "fill_v": 0.0, "fill_a": 0.0,
    })
    return BlobProgram(
        strip_length=10,
        duration=4.0,
        target_fps=30,
        requires_sync=True,
        buffer_sizes=[10, 4],
        pixel_views=[
            # dst view over the whole strip, identity both ways
            PixelViewSpec(buffer_idx=0, size=10, storage_identity=True,
                          has_physical=True, physical_identity=True),
            # dst view over a subset, non-identity physical map
            PixelViewSpec(buffer_idx=1, size=4, storage_identity=True,
                          has_physical=True, physical_identity=False,
                          physical_indices=[0, 4, 5, 9]),
            # work view, non-identity storage, no physical mapping
            PixelViewSpec(buffer_idx=0, size=3, storage_identity=False,
                          has_physical=False, physical_identity=False,
                          storage_indices=[7, 8, 9]),
        ],
        copy_ops=[
            CopyOpSpec(at=2.0, src_pixv_idx=0, dst_pixv_idx=2),
        ],
        layers=[
            BlobLayer(events=[
                BlobEvent(anim_type=ANIM_WAVE, start=0.0, duration=2.0,
                          dst_pixv_idx=0, params=wave_params),
                BlobEvent(anim_type=ANIM_SHIFT, start=2.0, duration=2.0,
                          dst_pixv_idx=0, src_pixv_idx=0, work_pixv_idx=2,
                          params=shift_params),
            ]),
            BlobLayer(events=[
                BlobEvent(anim_type=ANIM_SPARK, start=1.0, duration=0.5,
                          dst_pixv_idx=1,
                          params=pack_params("spark", {
                              "color_h": 0.0, "color_s": 0.0, "color_v": 1.0,
                              "fade": 0.25})),
            ]),
        ],
    )


class TestRoundTrip:
    def test_round_trips_to_equal(self):
        p = _sample_program()
        assert decode_blob(emit_blob(p)) == p

    def test_empty_layers_round_trip(self):
        p = BlobProgram(strip_length=5, duration=1.0)
        p.layers = [BlobLayer(), BlobLayer()]
        assert decode_blob(emit_blob(p)) == p


class TestHeader:
    def test_magic_and_version(self):
        blob = emit_blob(_sample_program())
        assert blob[:4] == BLOB_MAGIC == b"ELEM"
        assert blob[4] == BLOB_VERSION == 3

    def test_header_fields(self):
        blob = emit_blob(_sample_program())
        version, flags, target_fps, layer_count = struct.unpack_from("<BBBB", blob, 4)
        strip_length, buffer_count, view_count, copy_count = \
            struct.unpack_from("<HHHH", blob, 8)
        duration = struct.unpack_from("<f", blob, 16)[0]
        assert flags == 0x01            # requires_sync bit
        assert target_fps == 30
        assert layer_count == 2
        assert strip_length == 10
        assert buffer_count == 2
        assert view_count == 3
        assert copy_count == 1
        assert duration == 4.0

    def test_header_is_20_bytes(self):
        # First buffer size (u16=10) sits immediately after the 20-byte header.
        blob = emit_blob(_sample_program())
        assert struct.unpack_from("<H", blob, 20)[0] == 10

    def test_requires_sync_clear(self):
        p = _sample_program()
        p.requires_sync = False
        blob = emit_blob(p)
        assert blob[5] == 0x00


class TestPixelViewFlags:
    def test_identity_view_flags(self):
        p = decode_blob(emit_blob(_sample_program()))
        v0 = p.pixel_views[0]
        assert v0.storage_identity and v0.has_physical and v0.physical_identity
        assert v0.storage_indices is None and v0.physical_indices is None

    def test_non_identity_physical_array_preserved(self):
        p = decode_blob(emit_blob(_sample_program()))
        v1 = p.pixel_views[1]
        assert v1.has_physical and not v1.physical_identity
        assert v1.physical_indices == [0, 4, 5, 9]

    def test_storage_indices_preserved(self):
        p = decode_blob(emit_blob(_sample_program()))
        v2 = p.pixel_views[2]
        assert not v2.storage_identity and not v2.has_physical
        assert v2.storage_indices == [7, 8, 9]

    def test_malformed_physical_identity_rejected(self):
        bad = PixelViewSpec(buffer_idx=0, size=2, has_physical=False,
                            physical_identity=True)
        with pytest.raises(ValueError, match="physical_identity set without"):
            emit_blob(BlobProgram(strip_length=2, duration=1.0,
                                  buffer_sizes=[2], pixel_views=[bad]))

    def test_storage_index_length_mismatch_rejected(self):
        bad = PixelViewSpec(buffer_idx=0, size=3, storage_identity=False,
                            storage_indices=[0, 1])
        with pytest.raises(ValueError, match="storage_indices"):
            emit_blob(BlobProgram(strip_length=3, duration=1.0,
                                  buffer_sizes=[3], pixel_views=[bad]))


class TestEvents:
    def test_pixv_none_default(self):
        e = BlobEvent(anim_type=ANIM_WAVE, start=0.0, duration=1.0, dst_pixv_idx=0)
        assert e.src_pixv_idx == PIXV_NONE and e.work_pixv_idx == PIXV_NONE

    def test_event_view_indices_round_trip(self):
        p = decode_blob(emit_blob(_sample_program()))
        shift = p.layers[0].events[1]
        assert shift.anim_type == ANIM_SHIFT
        assert shift.src_pixv_idx == 0
        assert shift.dst_pixv_idx == 0
        assert shift.work_pixv_idx == 2

    def test_shift_params_have_no_buffer_id(self):
        # v3 shift param block is 22 bytes (v2 had a trailing buffer_id byte).
        params = pack_params("shift", {
            "direction": 0, "velocity": 1.0, "circular": 1,
            "fill_h": 0.0, "fill_s": 0.0, "fill_v": 0.0, "fill_a": 0.0,
        })
        assert len(params) == 22


class TestCopyOps:
    def test_copy_op_round_trip(self):
        p = decode_blob(emit_blob(_sample_program()))
        assert len(p.copy_ops) == 1
        op = p.copy_ops[0]
        assert op.at == 2.0 and op.src_pixv_idx == 0 and op.dst_pixv_idx == 2


class TestParams:
    def test_wave_param_round_trip(self):
        raw = pack_params("wave", {
            "channel": 1, "h": 120.0, "s": 1.0, "v": 0.5,
            "min_val": 0.0, "max_val": 1.0,
            "period": 2.0, "phase0": 0.0, "pixel_step": 0.25,
        })
        d = decode_params(ANIM_WAVE, raw)
        assert d["channel"] == 1 and d["h"] == 120.0 and d["period"] == 2.0

    def test_paint_solid_param_round_trip(self):
        raw = pack_params("paint", {"mode": 0, "color_h": 0.0, "color_s": 1.0,
                                     "color_v": 1.0, "color_a": 1.0})
        d = decode_params(ANIM_PAINT, raw)
        assert d["mode"] == 0 and d["color_s"] == 1.0

    def test_paint_per_pixel_param_round_trip(self):
        raw = pack_params("paint", {"mode": 1, "pixels": [
            (0.0, 1.0, 1.0, 1.0), (120.0, 1.0, 0.5, 0.25)]})
        d = decode_params(ANIM_PAINT, raw)
        assert d["mode"] == 1 and d["pixel_count"] == 2
        assert d["pixels"][1]["h"] == 120.0 and d["pixels"][1]["a"] == 0.25

    def test_paint_per_pixel_over_255_round_trip(self):
        # The per-pixel count is a u16, so > 255 colors round-trip cleanly.
        pixels = [(float(i % 360), 1.0, 1.0, 1.0) for i in range(300)]
        raw = pack_params("paint", {"mode": 1, "pixels": pixels})
        assert len(raw) == 3 + 300 * 16   # mode(1) + u16 count(2) + 300 hsva
        d = decode_params(ANIM_PAINT, raw)
        assert d["pixel_count"] == 300
        assert d["pixels"][299]["h"] == float(299 % 360)
