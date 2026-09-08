"""Elements v4 compiler — the full pipeline.

Pipeline:
    1. Validation (early) — bounds, params completeness
    2. Time resolution    — beats/sec -> absolute seconds -> whole milliseconds
    3. Layer inference    — events -> layers (time bin-packing, no merged map)
    4. Buffer + view planning — pool buffers, PixelViewSpec records, per-event
       view indices, and copy ops that keep source data alive
    5. Source resolution + required_start_ms via forward data-position tracing
    6. Validation (late)  — timing checks
    7. Safe interval analysis — dependency-aware per-strip safe intervals
    8. Param resolution   — resolve animation params to binary-ready values
    9. Blob emission      — serialize to the binary blob format

The byte layout lives in blob.py (docs/blob_format.md), enforced by
src/core/decoder.cpp. Structural caps mirror src/core/blob_limits.h via limits.py.
"""

from __future__ import annotations
import colorsys
import math
import warnings
from dataclasses import dataclass
from typing import Any

from .types import (
    SecMarker, AnimDef, PixelGroup, StripDef, COLORS,
    ANIM_TYPES, TIME_PARAMS, REQUIRED_PARAMS, STATEFUL_TYPES,
    CHANNELS, DIRECTIONS,
    CompiledStripArtifact, CompiledManifest, MemoryEstimate, ms_from_seconds,
)
from .blob import (
    BlobProgram, BlobLayer, BlobEvent, PixelViewSpec, CopyOpSpec,
    emit_blob, pack_params, PIXV_NONE,
)
from .memory_estimate import estimate_memory
from . import limits

# Bytes per HSVA pixel in the device pixel pool (matches sizeof(hsva_t)).
_HSVA_BYTES = 16


class CompileError(Exception):
    pass


def _check_finite(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CompileError(f"{field} must be a number")

    value_f = float(value)
    if not math.isfinite(value_f):
        raise CompileError(f"{field} must be finite")

    return value_f


def _check_range(value: Any, field: str, lo: float, hi: float) -> float:
    value_f = _check_finite(value, field)
    if not lo <= value_f <= hi:
        raise CompileError(f"{field} must be in [{lo:g}, {hi:g}], got {value_f:g}")
    return value_f


def _ensure_finite_positive(value: Any, field: str, *, allow_zero: bool = False) -> float:
    """Validate and normalize user-facing timing inputs."""
    value_f = _check_finite(value, field)

    if value_f < 0 or (not allow_zero and value_f == 0.0):
        raise CompileError(f"{field} must be > 0")

    return value_f


def _fmt_ms(ms: int) -> str:
    """Timeline value for messages: 700 -> '0.7s'."""
    return f"{ms / 1000:g}s"


# ---------------------------------------------------------------------------
# 1a. Early validation (before any processing)
# ---------------------------------------------------------------------------

def _validate_early(events: list[dict], strips: list[StripDef]):
    """Validate pixel bounds and param completeness before processing."""
    strip_map = {s.name: s for s in strips}

    # Duplicate strip names
    seen_names = set()
    for s in strips:
        if s.name in seen_names:
            raise CompileError(f"duplicate strip name '{s.name}'")
        seen_names.add(s.name)

    # Empty program
    if not events:
        raise CompileError("empty program: no events")

    for e in events:
        px = e["pixels"]

        # Unknown strip
        if px.strip_name not in strip_map:
            raise CompileError(f"unknown strip '{px.strip_name}'")

        # Pixel bounds
        strip_len = strip_map[px.strip_name].length
        for idx in px.indices:
            if idx < 0 or idx >= strip_len:
                raise CompileError(
                    f"pixel index {idx} out of bounds for strip "
                    f"'{px.strip_name}' (length {strip_len})"
                )

        # Param completeness
        anim = e["anim"]
        required = REQUIRED_PARAMS.get(anim.anim_type)
        if required is None:
            raise CompileError(f"unknown animation type '{anim.anim_type}'")
        for param_name in required:
            if param_name not in anim.params:
                raise CompileError(
                    f"{anim.anim_type} missing required param '{param_name}'"
                )

        # Only stateful animations read a source; on anything else source=
        # would be silently ignored, so reject it rather than mislead.
        if e.get("source") is not None and anim.anim_type not in STATEFUL_TYPES:
            raise CompileError(
                f"source= is only supported on shift events, not "
                f"'{anim.anim_type}'"
            )

        # Paint-specific validation
        if anim.anim_type == "paint":
            has_color = "color" in anim.params
            has_colors = "colors" in anim.params
            if not has_color and not has_colors:
                raise CompileError("paint requires either 'color' or 'colors'")
            if has_color and has_colors:
                raise CompileError("paint cannot have both 'color' and 'colors'")
            fmt = anim.params.get("format", "hsv")
            if fmt not in ("hsv", "rgb"):
                raise CompileError(f"paint format must be 'hsv' or 'rgb', got '{fmt}'")
            if has_colors:
                colors = anim.params["colors"]
                if not isinstance(colors, list):
                    raise CompileError("paint 'colors' must be a list")
                expected = len(px.indices)
                if len(colors) != expected:
                    raise CompileError(
                        f"paint 'colors' length {len(colors)} != pixel group "
                        f"size {expected}"
                    )
                for ci, c in enumerate(colors):
                    if not isinstance(c, (list, tuple)) or len(c) not in (3, 4):
                        raise CompileError(
                            f"paint colors[{ci}] must be a 3- or 4-tuple"
                        )
                    for vi, v in enumerate(c):
                        if not isinstance(v, (int, float)):
                            raise CompileError(
                                f"paint colors[{ci}][{vi}] must be numeric"
                            )

        # Event-level kwargs
        allowed_event_keys = {"anim", "pixels", "at", "duration", "source"}
        for key in e:
            if key in allowed_event_keys:
                continue
            if key == "snapshot":
                raise CompileError(
                    "snapshot is not supported; use source= when providing a "
                    "source event dependency"
                )
            raise CompileError(f"unknown event option '{key}'")


# ---------------------------------------------------------------------------
# 2. Time resolution
# ---------------------------------------------------------------------------

def _resolve_times(events: list[dict], beat: float, duration: float) -> int:
    """Resolve every event onto the millisecond grid; return the program length in ms.

    Beats scale by the beat length, sec() values are taken as written, and
    each absolute boundary (start, end, program end) is rounded once with
    ms_from_seconds. Everything downstream plans on the integers, so two
    events that meet in the source meet exactly in the blob.
    """
    beat = _ensure_finite_positive(beat, "beat")
    duration = _ensure_finite_positive(duration, "program duration")
    duration_ms = ms_from_seconds(duration)
    if duration_ms == 0:
        raise CompileError(f"program duration {duration}s rounds to 0 ms")

    for e in events:
        at, ev_dur = e["at"], e["duration"]
        at_in_sec = isinstance(at, SecMarker)
        dur_in_sec = isinstance(ev_dur, SecMarker)
        at = _ensure_finite_positive(at.seconds if at_in_sec else at,
                                     "event start time", allow_zero=True)
        ev_dur = _ensure_finite_positive(ev_dur.seconds if dur_in_sec else ev_dur,
                                         "event duration")

        # A sec() value never passes through beats, so it lands on the same
        # ms whatever the beat. Beat-authored ends are summed in beats before
        # scaling, so adjacent beat-authored events share one double. Round
        # boundaries, never lengths: rounding start and duration separately
        # could land the end one ms off the next start.
        at_sec = at if at_in_sec else at * beat
        if at_in_sec or dur_in_sec:
            end_sec = at_sec + (ev_dur if dur_in_sec else ev_dur * beat)
        else:
            end_sec = (at + ev_dur) * beat
        e["at_ms"] = ms_from_seconds(at_sec)
        e["end_ms"] = ms_from_seconds(end_sec)
        if e["end_ms"] <= e["at_ms"]:
            raise CompileError(
                f"{e['anim'].anim_type} event at {at_sec}s lasting "
                f"{end_sec - at_sec}s is shorter than 1 ms"
            )

    # Resolve time-based animation params
    for e in events:
        anim = e["anim"]
        resolved_params = dict(anim.params)

        # Time params: beats -> seconds
        for param_name in TIME_PARAMS.get(anim.anim_type, []):
            if param_name in resolved_params:
                val = resolved_params[param_name]
                if isinstance(val, SecMarker):
                    resolved_params[param_name] = val.seconds
                else:
                    resolved_params[param_name] = val * beat

        # Velocity: pixels/beat -> pixels/sec
        if anim.anim_type == "shift" and "velocity" in resolved_params:
            val = resolved_params["velocity"]
            if isinstance(val, SecMarker):
                resolved_params["velocity"] = val.seconds
            else:
                resolved_params["velocity"] = val / beat

        e["resolved_params"] = resolved_params

    return duration_ms


# ---------------------------------------------------------------------------
# 3. Layer inference — time bin-packing
# ---------------------------------------------------------------------------

def _overlaps(a: dict, b: dict) -> bool:
    return a["at_ms"] < b["end_ms"] and b["at_ms"] < a["end_ms"]


def _infer_layers(events: list[dict], max_layers: int = 32) -> list[list[dict]]:
    """Assign events to layers by first-fit time bin-packing.

    A layer is an ordered list of non-overlapping events; v3 has no merged
    layer index map (each event carries its own physical mapping on its dst
    view). Sets event['layer_idx'].
    """
    events_sorted = sorted(events, key=lambda e: e["at_ms"])
    layers: list[list[dict]] = []

    for e in events_sorted:
        placed = None
        for layer in layers:
            if not any(_overlaps(e, existing) for existing in layer):
                placed = layer
                break
        if placed is None:
            if len(layers) >= max_layers:
                raise CompileError(f"exceeded {max_layers} layer limit")
            placed = []
            layers.append(placed)
        placed.append(e)

    for li, layer in enumerate(layers):
        layer.sort(key=lambda e: e["at_ms"])
        for e in layer:
            e["layer_idx"] = li

    return layers


# ---------------------------------------------------------------------------
# 4/5. Source resolution
# ---------------------------------------------------------------------------

def _resolve_sources(events: list[dict], layers: list[list[dict]]):
    """Resolve and validate event['source'] into event['source_event'].

    Keeps the v2 validations: the source must be a scheduled AnimDef on a
    single layer, an instance of it must end by the dependent's start, it
    must cover the dependent's pixels, and it must sit on a layer <= the
    dependent's. The artifact is the source event itself; how its data
    reaches the dependent (direct view reuse or a copy op) is decided in
    buffer/view planning.
    """
    anim_to_layers: dict[int, set[int]] = {}
    for li, layer in enumerate(layers):
        for e in layer:
            anim_to_layers.setdefault(id(e["anim"]), set()).add(li)

    anim_to_events: dict[int, list[dict]] = {}
    for e in events:
        anim_to_events.setdefault(id(e["anim"]), []).append(e)

    for e in events:
        source_anim = e.get("source")
        if source_anim is None:
            e["source_event"] = None
            continue

        if not isinstance(source_anim, AnimDef):
            raise CompileError(
                f"{e['anim'].anim_type} event at {_fmt_ms(e['at_ms'])} has invalid source "
                f"value {source_anim!r}; expected an AnimDef"
            )

        source_aid = id(source_anim)
        if source_aid not in anim_to_layers:
            raise CompileError(
                f"{e['anim'].anim_type} at {_fmt_ms(e['at_ms'])} uses source= "
                f"{source_anim.anim_type}, but it has no scheduled events"
            )

        source_layers = sorted(anim_to_layers[source_aid])
        if len(source_layers) > 1:
            raise CompileError(
                f"source AnimDef {source_anim.anim_type} appears on multiple layers "
                f"({', '.join(map(str, source_layers))}); use a separate "
                f"AnimDef per source reference"
            )

        source_li = source_layers[0]
        dep_li = e["layer_idx"]

        source_events = [se for se in anim_to_events[source_aid]
                         if se["layer_idx"] == source_li]
        source_evt = max(
            (se for se in source_events if se["end_ms"] <= e["at_ms"]),
            key=lambda se: se["end_ms"],
            default=None,
        )
        if source_evt is None:
            raise CompileError(
                f"{e['anim'].anim_type} at {_fmt_ms(e['at_ms'])} uses source= "
                f"{source_anim.anim_type} on layer {source_li}, but no source event "
                f"has ended by the shift start"
            )

        if not set(e["pixels"].indices).issubset(set(source_evt["pixels"].indices)):
            missing = sorted(set(e["pixels"].indices) - set(source_evt["pixels"].indices))
            raise CompileError(
                f"{e['anim'].anim_type} at {_fmt_ms(e['at_ms'])} uses source= "
                f"{source_anim.anim_type}, but source event (ended at "
                f"{_fmt_ms(source_evt['end_ms'])}) does not cover pixels {missing}"
            )

        if source_li > dep_li:
            raise CompileError(
                f"{e['anim'].anim_type} on layer {dep_li} declares source= "
                f"{source_anim.anim_type} on layer {source_li}, but source_layer "
                f"must be <= dependent layer"
            )

        e["source_event"] = source_evt


# ---------------------------------------------------------------------------
# 6. Late validation
# ---------------------------------------------------------------------------

def _validate_late(events: list[dict], duration_ms: int):
    """Validate timing after layer inference; clamp events past the duration."""
    for e in events:
        # An event starting at or after program end cannot play a frame, and
        # clamping it would yield a zero-duration event the decoder rejects.
        if e["at_ms"] >= duration_ms:
            raise CompileError(
                f"{e['anim'].anim_type} event starts at {_fmt_ms(e['at_ms'])} "
                f"but program duration is {_fmt_ms(duration_ms)}"
            )

        if e["end_ms"] > duration_ms:
            warnings.warn(
                f"{e['anim'].anim_type} event ends at {_fmt_ms(e['end_ms'])} "
                f"but program duration is {_fmt_ms(duration_ms)} — clamping",
                stacklevel=2,
            )
            e["end_ms"] = duration_ms


# ---------------------------------------------------------------------------
# 4. Buffer + view planning
# ---------------------------------------------------------------------------

def _pixels(e: dict) -> list[int]:
    return e["pixels"].indices


def _physical_identity(indices: list[int], strip_length: int) -> bool:
    """True when view slot i maps straight to LED i (and fits the strip)."""
    n = len(indices)
    return n <= strip_length and indices == list(range(n))


def _is_stateful(e: dict) -> bool:
    return e["anim"].anim_type in STATEFUL_TYPES


@dataclass(frozen=True)
class BufferPixelPos:
    """One pixel slot in a logical buffer, used for source-data bookkeeping.

    `buffer` is a logical buffer id (e.g. ("dst", layer_idx) or
    ("preserve", copy_index)), assigned a real pool slot later. `slot` is the
    pixel index within that buffer.
    """
    buffer: Any
    slot: int


def _plan_source_copies(ordered: list[dict]) -> list[dict]:
    """Decide how each shift gets its source data; set per-event _src_kind.

    Walks events in time order, tracking the last event to write each dst
    buffer slot. A shift reads its source in place when every slot it needs
    still holds the source's output; otherwise that output would be
    overwritten before the read, so a copy op preserves it first. The copy is
    scheduled at the source's end, where the runtime has just rendered the
    source's endpoint sample. Returns the list of planned copy ops.
    """
    last_writer: dict[BufferPixelPos, dict] = {}
    copy_plan: list[dict] = []

    for e in ordered:
        li = e["layer_idx"]
        if _is_stateful(e):
            source_evt = e.get("source_event")
            if source_evt is None:
                # No source: read whatever currently sits in our own dst buffer.
                e["_src_kind"] = "self"
                e["_src_positions"] = [BufferPixelPos(("dst", li), j)
                                       for j in range(len(_pixels(e)))]
            else:
                src_pixels = source_evt["pixels"].indices
                positions = [BufferPixelPos(("dst", source_evt["layer_idx"]),
                                            src_pixels.index(p))
                             for p in _pixels(e)]
                if all(last_writer.get(pos) is source_evt for pos in positions):
                    e["_src_kind"] = "direct"
                else:
                    copy = {
                        "index": len(copy_plan),
                        "at": source_evt["end_ms"],
                        "read_at": e["at_ms"],
                        "size": len(_pixels(e)),
                        "source_evt": source_evt,
                        "positions": positions,
                    }
                    copy_plan.append(copy)
                    e["_src_kind"] = "copy"
                    e["_copy"] = copy
                e["_source_evt"] = source_evt
                e["_src_positions"] = positions
        else:
            e["_src_kind"] = "none"

        for slot in range(len(_pixels(e))):
            last_writer[BufferPixelPos(("dst", li), slot)] = e

    return copy_plan


def _compute_required_starts(ordered: list[dict], copy_plan: list[dict]):
    """Set required_start_ms on every event via forward data-position tracing.

    data_start[pos] is the earliest start time the data now at pos depends on.
    An event writing its dst stamps its own required start; a copy op carries
    the stamp from source to preserve buffer; a shift inherits the earliest
    stamp across the positions it reads. Copies are processed before event
    starts at the same time, matching the runtime, which samples ending events
    first, then runs that time's copies, then starts events.
    """
    data_start: dict[BufferPixelPos, float] = {}
    timeline = [("copy", c["at"], 0, c) for c in copy_plan]
    timeline += [("event", e["at_ms"], 1, e) for e in ordered]
    timeline.sort(key=lambda item: (item[1], item[2]))

    for kind, _t, _tie, obj in timeline:
        if kind == "copy":
            copy = obj
            for j, src_pos in enumerate(copy["positions"]):
                data_start[BufferPixelPos(("preserve", copy["index"]), j)] = \
                    data_start.get(src_pos, copy["at"])
            continue

        e = obj
        size = len(_pixels(e))
        src_kind = e["_src_kind"]
        if src_kind in ("self", "direct"):
            positions = e["_src_positions"]
        elif src_kind == "copy":
            positions = [BufferPixelPos(("preserve", e["_copy"]["index"]), j)
                         for j in range(size)]
        else:
            positions = None

        if positions is None:
            e["required_start_ms"] = e["at_ms"]
        else:
            e["required_start_ms"] = min(
                (data_start.get(pos, e["at_ms"]) for pos in positions),
                default=e["at_ms"],
            )

        for slot in range(size):
            data_start[BufferPixelPos(("dst", e["layer_idx"]), slot)] = \
                e["required_start_ms"]


def _assign_pool_slots(logical: dict[Any, dict]) -> tuple[dict[Any, int], list[int]]:
    """Map logical buffers onto pool slots, reusing across disjoint lifetimes.

    Greedy: a slot is reused only when it is free strictly before the new
    buffer's first write, so no frame shares a slot between a write and a
    source read. Returns (pool index per logical buffer, pool buffer sizes).
    """
    pool: list[dict] = []
    pool_idx: dict[Any, int] = {}
    for bid, info in sorted(logical.items(), key=lambda kv: (kv[1]["lo"], kv[1]["hi"])):
        chosen = None
        for slot in pool:
            if slot["free_at"] < info["lo"]:
                chosen = slot
                break
        if chosen is None:
            chosen = {"size": 0, "free_at": float("-inf"), "idx": len(pool)}
            pool.append(chosen)
        chosen["size"] = max(chosen["size"], info["size"])
        chosen["free_at"] = info["hi"]
        pool_idx[bid] = chosen["idx"]
    return pool_idx, [slot["size"] for slot in pool]


def _plan_buffers_and_views(layers: list[list[dict]], events: list[dict],
                            strip_length: int):
    """Plan pool buffers, pixel views, and copy ops; set per-event view indices
    and required_start_ms.

    Whole-buffer model: one dst buffer per layer (sized to the layer's largest
    event), one work buffer per stateful event, one preserve buffer per copy
    op. Buffers are pool slots reused across non-overlapping lifetimes.
    """
    ordered = sorted(events, key=lambda e: e["at_ms"])
    copy_plan = _plan_source_copies(ordered)
    _compute_required_starts(ordered, copy_plan)

    # --- Logical buffer lifetimes ---
    logical: dict[Any, dict] = {}

    def reg(bid: Any, size: int, lo: float, hi: float):
        b = logical.get(bid)
        if b is None:
            logical[bid] = {"size": size, "lo": lo, "hi": hi}
        else:
            b["size"] = max(b["size"], size)
            b["lo"] = min(b["lo"], lo)
            b["hi"] = max(b["hi"], hi)

    for li, layer in enumerate(layers):
        for e in layer:
            reg(("dst", li), len(_pixels(e)), e["at_ms"], e["end_ms"])
            if _is_stateful(e):
                reg(("work", id(e)), len(_pixels(e)), e["at_ms"], e["end_ms"])
            if e.get("_src_kind") == "direct":
                # The source's dst buffer must survive intact until this read.
                reg(("dst", e["_source_evt"]["layer_idx"]), 0, e["at_ms"], e["at_ms"])

    for copy in copy_plan:
        reg(("preserve", copy["index"]), copy["size"], copy["at"], copy["read_at"])
        src_evt = copy["source_evt"]
        reg(("dst", src_evt["layer_idx"]), 0, src_evt["at_ms"], copy["at"])

    pool_idx, buffer_sizes = _assign_pool_slots(logical)

    # --- Pixel views (deduplicated) ---
    views: list[PixelViewSpec] = []
    view_key: dict[tuple, int] = {}

    def get_view(buffer_idx, size, storage_identity, has_physical,
                 physical_identity, storage_indices, physical_indices) -> int:
        key = (buffer_idx, size, storage_identity, has_physical, physical_identity,
               tuple(storage_indices) if storage_indices is not None else None,
               tuple(physical_indices) if physical_indices is not None else None)
        idx = view_key.get(key)
        if idx is not None:
            return idx
        idx = len(views)
        views.append(PixelViewSpec(
            buffer_idx=buffer_idx, size=size, storage_identity=storage_identity,
            has_physical=has_physical, physical_identity=physical_identity,
            storage_indices=list(storage_indices) if storage_indices is not None else None,
            physical_indices=list(physical_indices) if physical_indices is not None else None,
        ))
        view_key[key] = idx
        return idx

    def dst_view_of(e: dict) -> int:
        pixels = _pixels(e)
        size = len(pixels)
        phys_id = _physical_identity(pixels, strip_length)
        return get_view(pool_idx[("dst", e["layer_idx"])], size,
                        True, True, phys_id, None, None if phys_id else pixels)

    def selector_view(source_evt: dict, positions: list[BufferPixelPos]) -> int:
        """A no-physical view selecting a shift's pixels out of a source buffer."""
        storage = [pos.slot for pos in positions]
        identity = storage == list(range(len(storage)))
        return get_view(pool_idx[("dst", source_evt["layer_idx"])], len(storage),
                        identity, False, False,
                        None if identity else storage, None)

    copy_ops: list[CopyOpSpec] = []
    for copy in copy_plan:
        src_idx = selector_view(copy["source_evt"], copy["positions"])
        dst_idx = get_view(pool_idx[("preserve", copy["index"])], copy["size"],
                           True, False, False, None, None)
        copy_ops.append(CopyOpSpec(at=copy["at"], src_pixv_idx=src_idx, dst_pixv_idx=dst_idx))
        copy["_preserve_view"] = dst_idx
    copy_ops.sort(key=lambda op: op.at)

    for e in events:
        e["dst_idx"] = dst_view_of(e)
        if not _is_stateful(e):
            e["src_idx"] = PIXV_NONE
            e["work_idx"] = PIXV_NONE
            continue

        e["work_idx"] = get_view(pool_idx[("work", id(e))], len(_pixels(e)),
                                 True, False, False, None, None)
        src_kind = e["_src_kind"]
        if src_kind == "self":
            e["src_idx"] = e["dst_idx"]
        elif src_kind == "direct":
            source_evt = e["_source_evt"]
            if _pixels(e) == source_evt["pixels"].indices:
                # Whole-source read: reuse the source's own dst view.
                e["src_idx"] = dst_view_of(source_evt)
            else:
                e["src_idx"] = selector_view(source_evt, e["_src_positions"])
        else:  # copy
            e["src_idx"] = e["_copy"]["_preserve_view"]

    return buffer_sizes, views, copy_ops


# ---------------------------------------------------------------------------
# 8. Resolve animation params to binary-ready values
# ---------------------------------------------------------------------------

def _convert_color_tuple(t: tuple, fmt: str, field: str) -> tuple[float, float, float, float]:
    """Convert a 3- or 4-tuple to (H, S, V, A) in HSV. H is 0-360."""
    if not isinstance(t, (list, tuple)) or len(t) not in (3, 4):
        raise CompileError(f"{field} must be a 3- or 4-tuple")
    a = _check_range(t[3], f"{field} alpha", 0.0, 1.0) if len(t) == 4 else 1.0
    if fmt == "rgb":
        r, g, b = (_check_range(c, f"{field} {name}", 0.0, 255.0) / 255.0
                   for c, name in zip(t, "rgb"))
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        return (h * 360.0, s, v, a)
    h = _check_finite(t[0], f"{field} hue")
    s = _check_range(t[1], f"{field} saturation", 0.0, 1.0)
    v = _check_range(t[2], f"{field} value", 0.0, 1.0)
    return (h, s, v, a)


def _resolve_color(name: str) -> tuple[float, float, float]:
    """Resolve a color name to (H, S, V)."""
    if name in COLORS:
        return COLORS[name]
    raise CompileError(f"unknown color '{name}'")


def _resolve_anim_params(event: dict) -> dict:
    """Convert resolved_params into binary-ready numeric values."""
    anim_type = event["anim"].anim_type
    p = dict(event["resolved_params"])

    if anim_type == "wave":
        if p["channel"] == "H":
            min_val = _check_finite(p["min_val"], "wave min_val")
            max_val = _check_finite(p["max_val"], "wave max_val")
        else:
            min_val = _check_range(p["min_val"], "wave min_val", 0.0, 1.0)
            max_val = _check_range(p["max_val"], "wave max_val", 0.0, 1.0)
        return {
            "channel": CHANNELS[p["channel"]],
            "h": _check_finite(p["h"], "wave h"),
            "s": _check_range(p["s"], "wave s", 0.0, 1.0),
            "v": _check_range(p["v"], "wave v", 0.0, 1.0),
            "min_val": min_val,
            "max_val": max_val,
            "period": _ensure_finite_positive(p["period"], "wave period"),
            "phase0": _check_finite(p["phase0"], "wave phase0"),
            "pixel_step": _check_finite(p["pixel_step"], "wave pixel_step"),
        }
    elif anim_type == "spark":
        color = p["color"]
        if isinstance(color, str):
            color_h, color_s, color_v = _resolve_color(color)
        else:
            color_h, color_s, color_v, _color_a = _convert_color_tuple(
                color, p.get("format", "hsv"), "spark color")
        return {
            "color_h": color_h,
            "color_s": color_s,
            "color_v": color_v,
            "fade": float(p["fade"]),
        }
    elif anim_type == "shift":
        fill_h, fill_s, fill_v = _resolve_color(p.get("fill", "transparent"))
        fill_a = 0.0 if p.get("fill") == "transparent" else 1.0
        return {
            "direction": DIRECTIONS[p["direction"]],
            "velocity": float(p["velocity"]),
            "circular": 1 if p.get("circular", False) else 0,
            "fill_h": fill_h,
            "fill_s": fill_s,
            "fill_v": fill_v,
            "fill_a": fill_a,
        }
    elif anim_type == "paint":
        fmt = p.get("format", "hsv")
        if "colors" in p:
            pixels = [_convert_color_tuple(c, fmt, f"paint colors[{i}]")
                      for i, c in enumerate(p["colors"])]
            return {"mode": 1, "pixels": pixels}
        else:
            color = p["color"]
            if isinstance(color, str):
                h, s, v = _resolve_color(color)
                return {"mode": 0, "color_h": h, "color_s": s, "color_v": v, "color_a": 1.0}
            else:
                h, s, v, a = _convert_color_tuple(color, fmt, "paint color")
                return {"mode": 0, "color_h": h, "color_s": s, "color_v": v, "color_a": a}
    elif anim_type == "pacifica":
        speed = float(p.get("speed", 1.0))
        brightness = float(p.get("brightness", 1.0))
        hue_shift = float(p.get("hue_shift", 0.0))
        if not math.isfinite(speed) or speed <= 0:
            raise CompileError(f"pacifica speed must be > 0, got {speed}")
        if not math.isfinite(brightness) or not (0.0 <= brightness <= 1.0):
            raise CompileError(
                f"pacifica brightness must be in [0, 1], got {brightness}"
            )
        if not math.isfinite(hue_shift):
            raise CompileError("pacifica hue_shift must be finite")
        return {"speed": speed, "brightness": brightness, "hue_shift": hue_shift}
    else:
        raise CompileError(f"unknown animation type '{anim_type}'")


# ---------------------------------------------------------------------------
# 7. Safe interval analysis
# ---------------------------------------------------------------------------

def _find_safe_intervals(events: list[dict], duration_ms: int) -> list[tuple[int, int]]:
    """Find time ranges where rebuilding engine state from scratch is safe.

    An event's unsafe span is [required_start_ms, end_ms). required_start_ms
    reaches back through source dependencies, so the gap before a dependent
    event is correctly marked unsafe.
    """
    unsafe = [(e["required_start_ms"], e["end_ms"]) for e in events]
    if not unsafe:
        return [(0, duration_ms)]

    unsafe.sort()
    merged = [list(unsafe[0])]
    for lo, hi in unsafe[1:]:
        if lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])

    safe = []
    prev_end = 0
    for lo, hi in merged:
        if lo > prev_end:
            safe.append((prev_end, lo))
        prev_end = max(prev_end, hi)
    if prev_end < duration_ms:
        safe.append((prev_end, duration_ms))

    # t=0 is always safe (engine reset at t=0 is correct by construction).
    if not safe or safe[0][0] > 0:
        safe.insert(0, (0, 0))

    return safe


def _intersect_intervals(
    a: list[tuple[int, int]], b: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    """Intersect two sorted lists of non-overlapping intervals."""
    result = []
    i = j = 0
    while i < len(a) and j < len(b):
        lo = max(a[i][0], b[j][0])
        hi = min(a[i][1], b[j][1])
        if lo < hi or (lo == hi == 0):  # preserve degenerate (0,0)
            result.append((lo, hi))
        if a[i][1] <= b[j][1]:
            i += 1
        else:
            j += 1
    return result


def _apply_width_filter(intervals: list[tuple[int, int]],
                        target_fps: int) -> list[tuple[int, int]]:
    """Drop intervals narrower than one frame period; always keep (0, 0).

    A safe interval narrower than 1000/target_fps ms cannot render even one
    frame before the next write lands, so it is not a usable seek target.
    """
    out = []
    for lo, hi in intervals:
        if lo == 0 and hi == 0:
            out.append((lo, hi))
        elif (hi - lo) * target_fps >= 1000:
            out.append((lo, hi))
    return out


# ---------------------------------------------------------------------------
# 9. Caps + assembly
# ---------------------------------------------------------------------------

def _check_caps(strip_length: int, buffer_sizes: list[int],
                pixel_views: list[PixelViewSpec], copy_ops: list[CopyOpSpec],
                blob_layers: list[BlobLayer]):
    """Reject programs the device decoder would reject (src/core/blob_limits.h)."""
    if strip_length < 1:
        raise CompileError(f"strip length must be at least 1, got {strip_length}")
    if strip_length > limits.MAX_STRIP_PIXELS:
        raise CompileError(
            f"strip length {strip_length} exceeds MAX_STRIP_PIXELS "
            f"{limits.MAX_STRIP_PIXELS}"
        )
    if len(blob_layers) > limits.MAX_LAYER_COUNT:
        raise CompileError(f"{len(blob_layers)} layers exceed MAX_LAYER_COUNT "
                           f"{limits.MAX_LAYER_COUNT}")
    if len(buffer_sizes) > limits.MAX_BUFFER_COUNT:
        raise CompileError(f"{len(buffer_sizes)} pool buffers exceed "
                           f"MAX_BUFFER_COUNT {limits.MAX_BUFFER_COUNT}")
    if len(pixel_views) > limits.MAX_PIXEL_VIEW_COUNT:
        raise CompileError(f"{len(pixel_views)} pixel views exceed "
                           f"MAX_PIXEL_VIEW_COUNT {limits.MAX_PIXEL_VIEW_COUNT}")
    if len(copy_ops) > limits.MAX_COPY_OP_COUNT:
        raise CompileError(f"{len(copy_ops)} copy ops exceed MAX_COPY_OP_COUNT "
                           f"{limits.MAX_COPY_OP_COUNT}")
    for size in buffer_sizes:
        if size > limits.MAX_STRIP_PIXELS:
            raise CompileError(f"pool buffer size {size} exceeds MAX_STRIP_PIXELS "
                               f"{limits.MAX_STRIP_PIXELS}")
    pool_bytes = sum(buffer_sizes) * _HSVA_BYTES
    if pool_bytes > limits.MAX_POOL_BYTES:
        raise CompileError(f"pixel pool {pool_bytes} bytes exceeds MAX_POOL_BYTES "
                           f"{limits.MAX_POOL_BYTES}")
    for li, layer in enumerate(blob_layers):
        if len(layer.events) > limits.MAX_EVENTS_PER_LAYER:
            raise CompileError(f"layer {li} has {len(layer.events)} events, exceeds "
                               f"MAX_EVENTS_PER_LAYER {limits.MAX_EVENTS_PER_LAYER}")
        for e in layer.events:
            if len(e.params) > limits.MAX_EVENT_PARAMS_BYTES:
                raise CompileError(f"{len(e.params)} param bytes exceed "
                                   f"MAX_EVENT_PARAMS_BYTES "
                                   f"{limits.MAX_EVENT_PARAMS_BYTES}")


def _compile_strip(strip_events: list[dict], strip_length: int, duration_ms: int,
                   target_fps: int, requires_sync: bool
                   ) -> tuple[bytes, list[tuple[int, int]], MemoryEstimate]:
    """Run the per-strip pipeline. Returns (blob, safe_intervals, memory)."""
    layers = _infer_layers(strip_events)
    _resolve_sources(strip_events, layers)
    _validate_late(strip_events, duration_ms)

    buffer_sizes, pixel_views, copy_ops = _plan_buffers_and_views(
        layers, strip_events, strip_length)

    blob_layers = []
    for layer in layers:
        blob_events = []
        for e in layer:
            params = pack_params(e["anim"].anim_type, _resolve_anim_params(e))
            blob_events.append(BlobEvent(
                anim_type=ANIM_TYPES[e["anim"].anim_type],
                start=e["at_ms"], duration=e["end_ms"] - e["at_ms"],
                dst_pixv_idx=e["dst_idx"], src_pixv_idx=e["src_idx"],
                work_pixv_idx=e["work_idx"], params=params,
            ))
        blob_layers.append(BlobLayer(events=blob_events))

    _check_caps(strip_length, buffer_sizes, pixel_views, copy_ops, blob_layers)

    program = BlobProgram(
        strip_length=strip_length, duration=duration_ms,
        target_fps=target_fps, requires_sync=requires_sync,
        buffer_sizes=buffer_sizes, pixel_views=pixel_views,
        copy_ops=copy_ops, layers=blob_layers,
    )
    safe_intervals = _find_safe_intervals(strip_events, duration_ms)
    return emit_blob(program), safe_intervals, estimate_memory(program)


# ---------------------------------------------------------------------------
# Main compile entry point
# ---------------------------------------------------------------------------

def _partition_by_strip(events: list[dict]) -> dict[str, list[dict]]:
    """Group events by strip name, preserving insertion order."""
    by_strip: dict[str, list[dict]] = {}
    for e in events:
        name = e["pixels"].strip_name
        if name not in by_strip:
            by_strip[name] = []
        by_strip[name].append(e)
    return by_strip


def _validate_program_config(target_fps: int, requires_sync: bool):
    """Validate the program-level header declarations."""
    if isinstance(target_fps, bool) or not isinstance(target_fps, int):
        raise CompileError("target_fps must be an integer")
    if not (1 <= target_fps <= 255):
        raise CompileError(f"target_fps must be in [1, 255], got {target_fps}")
    if not isinstance(requires_sync, bool):
        raise CompileError("requires_sync must be a bool")


def compile_manifest(strips: list[StripDef], events: list[dict],
                     beat: float, duration: float,
                     target_fps: int = 50,
                     requires_sync: bool = False) -> CompiledManifest:
    """Full compile pipeline: returns manifest with blobs + safe intervals."""
    _validate_program_config(target_fps, requires_sync)

    # Deep copy events so we don't mutate the builder's originals
    events = [dict(e) for e in events]

    # 1. Early validation (bounds, params) — across all strips
    _validate_early(events, strips)

    # 2. Time resolution — global, strip-independent
    duration_ms = _resolve_times(events, beat, duration)

    # 3–9. Per-strip pipeline; iterate input strips for canonical order.
    # Strip names are unique (enforced in _validate_early), so keying the
    # artifacts by strip_id never collides; insertion order stays canonical.
    by_strip = _partition_by_strip(events)
    strip_artifacts = {}
    per_strip_intervals = []
    for s in strips:
        strip_events = by_strip.get(s.name, [])
        blob, intervals, memory = _compile_strip(strip_events, s.length, duration_ms,
                                                 target_fps, requires_sync)
        strip_artifacts[s.name] = CompiledStripArtifact(
            strip_id=s.name, length=s.length, blob=blob, memory=memory,
        )
        per_strip_intervals.append(intervals)

    # Global safe interval intersection, then the frame-width filter.
    if per_strip_intervals:
        safe = per_strip_intervals[0]
        for si in per_strip_intervals[1:]:
            safe = _intersect_intervals(safe, si)
    else:
        safe = [(0, duration_ms)]
    safe = _apply_width_filter(safe, target_fps)

    return CompiledManifest(
        duration=duration,
        strips=strip_artifacts,
        safe_intervals=safe,
        target_fps=target_fps,
        requires_sync=requires_sync,
    )


def compile_program(strips: list[StripDef], events: list[dict],
                    beat: float, duration: float,
                    target_fps: int = 50,
                    requires_sync: bool = False) -> dict[str, bytes]:
    """Full compile pipeline: returns one binary blob per strip."""
    manifest = compile_manifest(strips, events, beat, duration,
                                target_fps=target_fps, requires_sync=requires_sync)
    return {a.strip_id: a.blob for a in manifest.strips.values()}
