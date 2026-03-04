"""Elements v2 binary blob serialization.

Blob format (little-endian):

Header (12 bytes):
    magic:            4 bytes   "ELEM"
    version:          uint8     2
    layer_count:      uint8
    buffer_count:     uint8
    max_remap_length: uint8
    duration:         float32   (seconds)

Buffer pool (1 byte per buffer):
    size: uint8   (pixel count)

Per layer:
    index_map_length: uint8
    index_map:        uint8[index_map_length]
    event_count:      uint16

Per event:
    anim_type:         uint8
    t_start:           float32
    duration:          float32
    source_layer:      uint8    (0xFF = none)
    remap_is_identity: uint8    (1 = identity, skip scatter copy)
    remap_length:      uint8
    remap:             uint8[remap_length]
    params_size:       uint8
    params:            (animation-specific bytes)

Animation params:
    Wave (33 bytes):
        channel uint8, h f32, s f32, v f32, min_val f32, max_val f32,
        period f32, phase0 f32, pixel_step f32

    Spark (16 bytes):
        color_h f32, color_s f32, color_v f32, fade f32

    Shift (23 bytes):
        direction uint8, velocity f32, circular uint8,
        fill_h f32, fill_s f32, fill_v f32, fill_a f32,
        buffer_id uint8
"""

from __future__ import annotations
import struct

from .types import ANIM_TYPES

# Animation type IDs (re-exported for tests)
ANIM_WAVE  = ANIM_TYPES["wave"]
ANIM_SHIFT = ANIM_TYPES["shift"]
ANIM_SPARK = ANIM_TYPES["spark"]
ANIM_FILL  = ANIM_TYPES["fill"]

BLOB_MAGIC = b"ELEM"
BLOB_VERSION = 2


def _pack_wave_params(p: dict) -> bytes:
    return struct.pack("<B8f",
        p["channel"],
        p["h"], p["s"], p["v"],
        p["min_val"], p["max_val"],
        p["period"], p["phase0"], p["pixel_step"],
    )


def _pack_spark_params(p: dict) -> bytes:
    return struct.pack("<4f",
        p["color_h"], p["color_s"], p["color_v"],
        p["fade"],
    )


def _pack_shift_params(p: dict) -> bytes:
    return struct.pack("<BfB4fB",
        p["direction"],
        p["velocity"],
        p["circular"],
        p["fill_h"], p["fill_s"], p["fill_v"], p["fill_a"],
        p["buffer_id"],
    )


PARAM_PACKERS = {
    "wave":  _pack_wave_params,
    "shift": _pack_shift_params,
    "spark": _pack_spark_params,
}


def emit_blob(layers: list[dict], buffer_pool: list[dict],
              duration: float, max_remap: int) -> bytes:
    """Serialize the compiled program into a binary blob."""
    buf = bytearray()

    # Header (12 bytes)
    buf += BLOB_MAGIC
    buf += struct.pack("<B", BLOB_VERSION)
    buf += struct.pack("<B", len(layers))
    buf += struct.pack("<B", len(buffer_pool))
    buf += struct.pack("<B", max_remap)
    buf += struct.pack("<f", duration)

    # Buffer pool
    for bp in buffer_pool:
        buf += struct.pack("<B", bp["size"])

    # Layers
    for layer in layers:
        indices = layer["indices"]
        events = layer["events"]

        # Index map
        buf += struct.pack("<B", len(indices))
        for idx in indices:
            buf += struct.pack("<B", idx)

        # Event count
        buf += struct.pack("<H", len(events))

        # Events
        for e in events:
            anim_type = e["anim"].anim_type
            packer = PARAM_PACKERS.get(anim_type)
            if packer is None:
                raise ValueError(f"no param packer for '{anim_type}'")

            params_bytes = packer(e["binary_params"])
            remap = e["index_remap"]

            # anim_type
            buf += struct.pack("<B", ANIM_TYPES[anim_type])

            # t_start, duration
            buf += struct.pack("<ff", e["at_sec"], e["duration_sec"])

            # source_layer (0xFF means none)
            buf += struct.pack("<B", e.get("source_layer", 0xFF))

            # remap_is_identity flag
            buf += struct.pack("<B", 1 if e.get("remap_is_identity", False) else 0)

            # remap
            buf += struct.pack("<B", len(remap))
            for r in remap:
                buf += struct.pack("<B", r)

            # params
            buf += struct.pack("<B", len(params_bytes))
            buf += params_bytes

    return bytes(buf)


# ---------------------------------------------------------------------------
# Decoder (for tests)
# ---------------------------------------------------------------------------

def decode_header(data: bytes) -> dict:
    """Decode blob header. Returns dict with header fields and offset."""
    magic = data[0:4]
    version, layer_count, buffer_count, max_remap = struct.unpack_from("<BBBB", data, 4)
    duration = struct.unpack_from("<f", data, 8)[0]
    return {
        "magic": magic,
        "version": version,
        "layer_count": layer_count,
        "buffer_count": buffer_count,
        "max_remap_length": max_remap,
        "duration": duration,
        "offset": 12,
    }


def decode_blob(data: bytes) -> dict:
    """Fully decode a blob. Returns structured dict."""
    hdr = decode_header(data)
    pos = hdr["offset"]

    # Buffer pool
    buffer_pool = []
    for _ in range(hdr["buffer_count"]):
        size = struct.unpack_from("<B", data, pos)[0]
        buffer_pool.append(size)
        pos += 1

    # Layers
    layers = []
    for _ in range(hdr["layer_count"]):
        # Index map
        idx_len = struct.unpack_from("<B", data, pos)[0]
        pos += 1
        index_map = list(struct.unpack_from(f"<{idx_len}B", data, pos))
        pos += idx_len

        # Event count
        event_count = struct.unpack_from("<H", data, pos)[0]
        pos += 2

        # Events
        events = []
        for _ in range(event_count):
            anim_type = struct.unpack_from("<B", data, pos)[0]
            pos += 1

            t_start, dur = struct.unpack_from("<ff", data, pos)
            pos += 8

            source_layer = struct.unpack_from("<B", data, pos)[0]
            pos += 1

            remap_is_identity = struct.unpack_from("<B", data, pos)[0]
            pos += 1

            remap_len = struct.unpack_from("<B", data, pos)[0]
            pos += 1
            remap = list(struct.unpack_from(f"<{remap_len}B", data, pos))
            pos += remap_len

            params_size = struct.unpack_from("<B", data, pos)[0]
            pos += 1
            params_raw = data[pos:pos + params_size]
            pos += params_size

            params = _decode_params(anim_type, params_raw)

            events.append({
                "anim_type": anim_type,
                "t_start": t_start,
                "duration": dur,
                "source_layer": source_layer,
                "remap_is_identity": bool(remap_is_identity),
                "remap": remap,
                "params": params,
            })

        layers.append({
            "index_map": index_map,
            "events": events,
        })

    return {
        "header": hdr,
        "buffer_pool": buffer_pool,
        "layers": layers,
    }


def _decode_params(anim_type: int, raw: bytes) -> dict:
    """Decode animation-specific params from raw bytes."""
    if anim_type == ANIM_WAVE:
        ch = struct.unpack_from("<B", raw, 0)[0]
        h, s, v, mn, mx, period, phase0, pstep = struct.unpack_from("<8f", raw, 1)
        return {
            "channel": ch, "h": h, "s": s, "v": v,
            "min_val": mn, "max_val": mx,
            "period": period, "phase0": phase0, "pixel_step": pstep,
        }
    elif anim_type == ANIM_SPARK:
        ch, cs, cv, fade = struct.unpack_from("<4f", raw, 0)
        return {"color_h": ch, "color_s": cs, "color_v": cv, "fade": fade}
    elif anim_type == ANIM_SHIFT:
        d = struct.unpack_from("<B", raw, 0)[0]
        vel = struct.unpack_from("<f", raw, 1)[0]
        circ = struct.unpack_from("<B", raw, 5)[0]
        fh, fs, fv, fa = struct.unpack_from("<4f", raw, 6)
        return {
            "direction": d, "velocity": vel, "circular": circ,
            "fill_h": fh, "fill_s": fs, "fill_v": fv, "fill_a": fa,
            "buffer_id": struct.unpack_from("<B", raw, 22)[0],
        }
    else:
        return {"raw": raw}
