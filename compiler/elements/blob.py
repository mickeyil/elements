"""Elements v3 binary blob serialization.

The byte layout is docs/blob_format.md, enforced by src/decoder.cpp; this
module is the emitter side. It serializes a BlobProgram (the compiler's
emit IR) into the exact bytes the device decoder accepts, and provides a
matching decoder used to round-trip the emitter in tests.

Section order (byte-packed, little-endian, no padding):
    header (20 B) | buffer sizes | pixel views | copy ops | layers + events

Animation params are type-specific bytes laid out by each src/animations/
factory; pack_params() produces them. The shift param block dropped v2's
trailing buffer_id byte: the work buffer is now the event's work_pixv_idx.
"""

from __future__ import annotations
import struct
from dataclasses import dataclass, field

from .types import ANIM_TYPES

BLOB_MAGIC = b"ELEM"
BLOB_VERSION = 3

# Sentinel for an absent pixel view index (matches src/runtime_constants.h).
PIXV_NONE = 0xFFFF

# Header flag bits.
_FLAG_REQUIRES_SYNC = 0x01

# Pixel view flag bits.
_VIEW_STORAGE_IDENTITY = 0x01
_VIEW_HAS_PHYSICAL = 0x02
_VIEW_PHYSICAL_IDENTITY = 0x04

# Animation type IDs (re-exported for tests).
ANIM_WAVE = ANIM_TYPES["wave"]
ANIM_SHIFT = ANIM_TYPES["shift"]
ANIM_SPARK = ANIM_TYPES["spark"]
ANIM_PAINT = ANIM_TYPES["paint"]


# ---------------------------------------------------------------------------
# Emit IR — what the compiler's planner hands the serializer
# ---------------------------------------------------------------------------

@dataclass
class PixelViewSpec:
    """One PixelView descriptor.

    Index arrays are present only when their identity flag is off:
    storage_indices iff not storage_identity; physical_indices iff
    has_physical and not physical_identity. Absent arrays stay None.
    """
    buffer_idx: int
    size: int
    storage_identity: bool = True
    has_physical: bool = False
    physical_identity: bool = False
    storage_indices: list[int] | None = None
    physical_indices: list[int] | None = None


@dataclass
class CopyOpSpec:
    at: float            # program-relative seconds
    src_pixv_idx: int
    dst_pixv_idx: int


@dataclass
class BlobEvent:
    anim_type: int       # AnimType enum value
    start: float         # program-relative seconds
    duration: float      # seconds
    dst_pixv_idx: int    # required; must reference a has_physical view
    src_pixv_idx: int = PIXV_NONE
    work_pixv_idx: int = PIXV_NONE
    params: bytes = b""  # animation-specific bytes from pack_params()


@dataclass
class BlobLayer:
    events: list[BlobEvent] = field(default_factory=list)


@dataclass
class BlobProgram:
    strip_length: int
    duration: float
    target_fps: int = 50
    requires_sync: bool = False
    buffer_sizes: list[int] = field(default_factory=list)
    pixel_views: list[PixelViewSpec] = field(default_factory=list)
    copy_ops: list[CopyOpSpec] = field(default_factory=list)
    layers: list[BlobLayer] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Animation param packing (dict -> bytes)
# ---------------------------------------------------------------------------

def _pack_wave_params(p: dict) -> bytes:
    return struct.pack("<B8f",
        p["channel"],
        p["h"], p["s"], p["v"],
        p["min_val"], p["max_val"],
        p["period"], p["phase0"], p["pixel_step"],
    )


def _pack_spark_params(p: dict) -> bytes:
    return struct.pack("<4f",
        p["color_h"], p["color_s"], p["color_v"], p["fade"],
    )


def _pack_shift_params(p: dict) -> bytes:
    return struct.pack("<BfB4f",
        p["direction"],
        p["velocity"],
        p["circular"],
        p["fill_h"], p["fill_s"], p["fill_v"], p["fill_a"],
    )


def _pack_paint_params(p: dict) -> bytes:
    if p["mode"] == 0:
        return struct.pack("<B4f",
            0, p["color_h"], p["color_s"], p["color_v"], p["color_a"],
        )
    pixels = p["pixels"]
    buf = struct.pack("<BH", 1, len(pixels))   # mode, u16 count
    for h, s, v, a in pixels:
        buf += struct.pack("<4f", h, s, v, a)
    return buf


_PARAM_PACKERS = {
    "wave": _pack_wave_params,
    "shift": _pack_shift_params,
    "spark": _pack_spark_params,
    "paint": _pack_paint_params,
}


def pack_params(anim_type: str, params: dict) -> bytes:
    """Pack binary-ready animation params into the blob's param bytes."""
    packer = _PARAM_PACKERS.get(anim_type)
    if packer is None:
        raise ValueError(f"no param packer for '{anim_type}'")
    return packer(params)


# ---------------------------------------------------------------------------
# Emit
# ---------------------------------------------------------------------------

def _view_flags(v: PixelViewSpec) -> int:
    """Validate a view's flag/array agreement and return its flag byte."""
    if not v.has_physical and v.physical_identity:
        raise ValueError("malformed view: physical_identity set without has_physical")

    flags = 0
    if v.storage_identity:
        flags |= _VIEW_STORAGE_IDENTITY
        if v.storage_indices:
            raise ValueError("storage_identity view must not carry storage_indices")
    else:
        if v.storage_indices is None or len(v.storage_indices) != v.size:
            raise ValueError("non-identity storage view needs size storage_indices")

    if v.has_physical:
        flags |= _VIEW_HAS_PHYSICAL
        if v.physical_identity:
            flags |= _VIEW_PHYSICAL_IDENTITY
            if v.physical_indices:
                raise ValueError("physical_identity view must not carry physical_indices")
        else:
            if v.physical_indices is None or len(v.physical_indices) != v.size:
                raise ValueError("non-identity physical view needs size physical_indices")
    elif v.physical_indices:
        raise ValueError("non-physical view must not carry physical_indices")

    return flags


def emit_blob(program: BlobProgram) -> bytes:
    """Serialize a BlobProgram into the v3 binary blob."""
    buf = bytearray()

    # Header (20 bytes)
    flags = _FLAG_REQUIRES_SYNC if program.requires_sync else 0
    buf += BLOB_MAGIC
    buf += struct.pack("<BBBB", BLOB_VERSION, flags, program.target_fps,
                       len(program.layers))
    buf += struct.pack("<HHHH", program.strip_length, len(program.buffer_sizes),
                       len(program.pixel_views), len(program.copy_ops))
    buf += struct.pack("<f", program.duration)

    # Buffer sizes
    for size in program.buffer_sizes:
        buf += struct.pack("<H", size)

    # Pixel views
    for v in program.pixel_views:
        buf += struct.pack("<HHB", v.buffer_idx, v.size, _view_flags(v))
        if not v.storage_identity:
            buf += struct.pack(f"<{v.size}H", *v.storage_indices)
        if v.has_physical and not v.physical_identity:
            buf += struct.pack(f"<{v.size}H", *v.physical_indices)

    # Copy ops
    for op in program.copy_ops:
        buf += struct.pack("<fHH", op.at, op.src_pixv_idx, op.dst_pixv_idx)

    # Layers and events
    for layer in program.layers:
        buf += struct.pack("<H", len(layer.events))
        for e in layer.events:
            buf += struct.pack("<Bff", e.anim_type, e.start, e.duration)
            buf += struct.pack("<HHH", e.src_pixv_idx, e.dst_pixv_idx, e.work_pixv_idx)
            buf += struct.pack("<H", len(e.params))
            buf += e.params

    return bytes(buf)


# ---------------------------------------------------------------------------
# Decode (for tests) — mirrors emit; keeps params as raw bytes so a
# BlobProgram round-trips to an equal value.
# ---------------------------------------------------------------------------

def decode_blob(data: bytes) -> BlobProgram:
    """Decode a v3 blob back into a BlobProgram. Raises on malformed input."""
    if data[0:4] != BLOB_MAGIC:
        raise ValueError("bad magic")
    version, flags, target_fps, layer_count = struct.unpack_from("<BBBB", data, 4)
    if version != BLOB_VERSION:
        raise ValueError(f"bad version {version}")
    strip_length, buffer_count, pixel_view_count, copy_op_count = \
        struct.unpack_from("<HHHH", data, 8)
    duration = struct.unpack_from("<f", data, 16)[0]
    pos = 20

    buffer_sizes = list(struct.unpack_from(f"<{buffer_count}H", data, pos))
    pos += 2 * buffer_count

    pixel_views = []
    for _ in range(pixel_view_count):
        buffer_idx, size, vflags = struct.unpack_from("<HHB", data, pos)
        pos += 5
        storage_identity = bool(vflags & _VIEW_STORAGE_IDENTITY)
        has_physical = bool(vflags & _VIEW_HAS_PHYSICAL)
        physical_identity = bool(vflags & _VIEW_PHYSICAL_IDENTITY)
        storage_indices = None
        physical_indices = None
        if not storage_identity:
            storage_indices = list(struct.unpack_from(f"<{size}H", data, pos))
            pos += 2 * size
        if has_physical and not physical_identity:
            physical_indices = list(struct.unpack_from(f"<{size}H", data, pos))
            pos += 2 * size
        pixel_views.append(PixelViewSpec(
            buffer_idx=buffer_idx, size=size,
            storage_identity=storage_identity,
            has_physical=has_physical, physical_identity=physical_identity,
            storage_indices=storage_indices, physical_indices=physical_indices,
        ))

    copy_ops = []
    for _ in range(copy_op_count):
        at, src, dst = struct.unpack_from("<fHH", data, pos)
        pos += 8
        copy_ops.append(CopyOpSpec(at=at, src_pixv_idx=src, dst_pixv_idx=dst))

    layers = []
    for _ in range(layer_count):
        event_count = struct.unpack_from("<H", data, pos)[0]
        pos += 2
        events = []
        for _ in range(event_count):
            anim_type, start, dur = struct.unpack_from("<Bff", data, pos)
            pos += 9
            src, dst, work = struct.unpack_from("<HHH", data, pos)
            pos += 6
            params_size = struct.unpack_from("<H", data, pos)[0]
            pos += 2
            params = bytes(data[pos:pos + params_size])
            pos += params_size
            events.append(BlobEvent(
                anim_type=anim_type, start=start, duration=dur,
                dst_pixv_idx=dst, src_pixv_idx=src, work_pixv_idx=work,
                params=params,
            ))
        layers.append(BlobLayer(events=events))

    if pos != len(data):
        raise ValueError(f"trailing bytes: parsed {pos} of {len(data)}")

    return BlobProgram(
        strip_length=strip_length, duration=duration,
        target_fps=target_fps, requires_sync=bool(flags & _FLAG_REQUIRES_SYNC),
        buffer_sizes=buffer_sizes, pixel_views=pixel_views,
        copy_ops=copy_ops, layers=layers,
    )


def decode_params(anim_type: int, raw: bytes) -> dict:
    """Decode animation param bytes into a dict, for test inspection."""
    if anim_type == ANIM_WAVE:
        ch = raw[0]
        h, s, v, mn, mx, period, phase0, pstep = struct.unpack_from("<8f", raw, 1)
        return {"channel": ch, "h": h, "s": s, "v": v,
                "min_val": mn, "max_val": mx,
                "period": period, "phase0": phase0, "pixel_step": pstep}
    if anim_type == ANIM_SPARK:
        ch, cs, cv, fade = struct.unpack_from("<4f", raw, 0)
        return {"color_h": ch, "color_s": cs, "color_v": cv, "fade": fade}
    if anim_type == ANIM_SHIFT:
        direction = raw[0]
        velocity = struct.unpack_from("<f", raw, 1)[0]
        circular = raw[5]
        fh, fs, fv, fa = struct.unpack_from("<4f", raw, 6)
        return {"direction": direction, "velocity": velocity, "circular": circular,
                "fill_h": fh, "fill_s": fs, "fill_v": fv, "fill_a": fa}
    if anim_type == ANIM_PAINT:
        mode = raw[0]
        if mode == 0:
            h, s, v, a = struct.unpack_from("<4f", raw, 1)
            return {"mode": 0, "color_h": h, "color_s": s, "color_v": v, "color_a": a}
        count = struct.unpack_from("<H", raw, 1)[0]
        pixels = []
        for i in range(count):
            h, s, v, a = struct.unpack_from("<4f", raw, 3 + i * 16)
            pixels.append({"h": h, "s": s, "v": v, "a": a})
        return {"mode": 1, "pixel_count": count, "pixels": pixels}
    return {"raw": raw}
