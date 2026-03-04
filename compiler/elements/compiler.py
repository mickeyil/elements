"""Elements v2 compiler — the full pipeline.

Pipeline:
    1. Validation (early) — bounds, params completeness
    2. Time resolution    — beats/sec → absolute seconds
    3. Layer inference     — events → layers (bin-packing with index merging)
    4. Buffer packing     — stateful animations → shared buffer slots
    5. Validation (late)  — snapshot references, timing checks
    6. Blob emission      — serialize to binary
"""

from __future__ import annotations
import math
import warnings
from typing import Any

from .types import (
    SecMarker, AnimDef, PixelGroup, StripDef, COLORS,
    ANIM_TYPES, TIME_PARAMS, REQUIRED_PARAMS, STATEFUL_TYPES,
    CHANNELS, DIRECTIONS,
)
from .blob import emit_blob


class CompileError(Exception):
    pass


def _ensure_finite_positive(value: Any, field: str, *, allow_zero: bool = False) -> float:
    """Validate and normalize user-facing timing inputs."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CompileError(f"{field} must be a number")

    value_f = float(value)
    if not math.isfinite(value_f):
        raise CompileError(f"{field} must be finite")

    if value_f < 0 or (not allow_zero and value_f == 0.0):
        raise CompileError(f"{field} must be > 0")

    return value_f


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


# ---------------------------------------------------------------------------
# 2. Time resolution
# ---------------------------------------------------------------------------

def _resolve_times(events: list[dict], beat: float, duration: float):
    """Resolve SecMarkers to beats, then convert all times to seconds."""
    beat = _ensure_finite_positive(beat, "beat")
    _ensure_finite_positive(duration, "program duration")

    for e in events:
        at = e["at"]
        ev_dur = e["duration"]
        sec_to_beats = 1.0 / beat

        if isinstance(at, SecMarker):
            at = _ensure_finite_positive(at.seconds * sec_to_beats, "event start time",
                                         allow_zero=True)
        else:
            at = _ensure_finite_positive(at, "event start time", allow_zero=True)

        if isinstance(ev_dur, SecMarker):
            ev_dur = _ensure_finite_positive(ev_dur.seconds * sec_to_beats, "event duration")
        else:
            ev_dur = _ensure_finite_positive(ev_dur, "event duration")

        e["at"] = at
        e["duration"] = ev_dur

        # Convert beats → seconds
        e["at_sec"] = e["at"] * beat
        e["duration_sec"] = e["duration"] * beat
        e["end_sec"] = e["at_sec"] + e["duration_sec"]

    # Resolve time-based animation params
    for e in events:
        anim = e["anim"]
        resolved_params = dict(anim.params)

        # Time params: beats → seconds
        for param_name in TIME_PARAMS.get(anim.anim_type, []):
            if param_name in resolved_params:
                val = resolved_params[param_name]
                if isinstance(val, SecMarker):
                    resolved_params[param_name] = val.seconds
                else:
                    resolved_params[param_name] = val * beat

        # Velocity: pixels/beat → pixels/sec
        if anim.anim_type == "shift" and "velocity" in resolved_params:
            val = resolved_params["velocity"]
            if isinstance(val, SecMarker):
                resolved_params["velocity"] = val.seconds
            else:
                resolved_params["velocity"] = val / beat

        e["resolved_params"] = resolved_params


# ---------------------------------------------------------------------------
# 3. Layer inference — bin-packing with index map merging
# ---------------------------------------------------------------------------

def _overlaps(a: dict, b: dict) -> bool:
    return a["at_sec"] < b["end_sec"] and b["at_sec"] < a["end_sec"]


def _infer_layers(events: list[dict], max_layers: int = 32) -> list[dict]:
    """Assign events to layers using greedy bin-packing with index merging."""
    events_sorted = sorted(events, key=lambda e: e["at_sec"])
    layers = []

    for e in events_sorted:
        best = None
        best_size = float('inf')

        for layer in layers:
            # Check time overlap with all events in this layer
            if any(_overlaps(e, existing) for existing in layer["events"]):
                continue

            # Prefer smallest merged index map
            merged = set(layer["indices"]) | set(e["pixels"].indices)
            if len(merged) < best_size:
                best = layer
                best_size = len(merged)

        if best:
            best["indices"] = sorted(set(best["indices"]) | set(e["pixels"].indices))
            best["events"].append(e)
            e["index_remap"] = [best["indices"].index(i) for i in e["pixels"].indices]
        else:
            if len(layers) >= max_layers:
                raise CompileError(f"exceeded {max_layers} layer limit")
            new_layer = {
                "indices": list(e["pixels"].indices),
                "events": [e],
            }
            layers.append(new_layer)
            e["index_remap"] = list(range(len(e["pixels"].indices)))

    # Sort events within each layer by start time
    for layer in layers:
        layer["events"].sort(key=lambda e: e["at_sec"])

    # Set remap_is_identity flag per event
    for layer in layers:
        layer_len = len(layer["indices"])
        for e in layer["events"]:
            remap = e["index_remap"]
            e["remap_is_identity"] = (
                len(remap) == layer_len
                and remap == list(range(layer_len))
            )

    return layers


# ---------------------------------------------------------------------------
# 4. Buffer packing
# ---------------------------------------------------------------------------

def _needs_buffer(anim_type: str) -> bool:
    return anim_type in STATEFUL_TYPES


def _pack_buffers(layers: list[dict]) -> list[dict]:
    """Assign shared buffer slots to stateful animations."""
    stateful = []
    for layer in layers:
        for e in layer["events"]:
            if _needs_buffer(e["anim"].anim_type):
                stateful.append({
                    "event": e,
                    "at_sec": e["at_sec"],
                    "end_sec": e["end_sec"],
                    "size": len(e["pixels"].indices),
                })

    if not stateful:
        return []

    stateful.sort(key=lambda s: s["at_sec"])
    slots = []

    for s in stateful:
        placed = False
        for slot in slots:
            if s["at_sec"] >= slot["end"]:
                slot["end"] = s["end_sec"]
                slot["size"] = max(slot["size"], s["size"])
                slot["usages"].append(s)
                s["event"]["buffer_id"] = slot["id"]
                placed = True
                break

        if not placed:
            slot_id = len(slots)
            slots.append({
                "id": slot_id,
                "end": s["end_sec"],
                "size": s["size"],
                "usages": [s],
            })
            s["event"]["buffer_id"] = slot_id

    return [{"id": s["id"], "size": s["size"]} for s in slots]


# ---------------------------------------------------------------------------
# 5. Late validation (after layer inference)
# ---------------------------------------------------------------------------

def _validate_late(events: list[dict], layers: list[dict],
                   buffer_pool: list[dict], duration: float):
    """Validate timing and snapshot references after processing."""
    for e in events:
        # Event starts after duration → error
        if e["at_sec"] > duration:
            raise CompileError(
                f"{e['anim'].anim_type} event starts at {e['at_sec']}s "
                f"but program duration is {duration}s"
            )

        # Event extends past duration → warning + clamp
        if e["end_sec"] > duration + 1e-6:
            warnings.warn(
                f"{e['anim'].anim_type} event ends at {e['end_sec']:.3f}s "
                f"but program duration is {duration}s — clamping",
                stacklevel=2,
            )
            e["end_sec"] = duration
            e["duration_sec"] = duration - e["at_sec"]

    # Snapshot validation
    for e in events:
        if "snapshot" not in e:
            continue

        source_anim = e["snapshot"]
        shift_start = e["at_sec"]
        shift_pixels = set(e["pixels"].indices)

        source_events = [ev for ev in events if ev["anim"] is source_anim]
        if not source_events:
            raise CompileError(
                f"snapshot references {source_anim.anim_type} "
                f"but it has no scheduled events"
            )

        valid = False
        for se in source_events:
            if se["end_sec"] <= shift_start + 1e-6:
                if shift_pixels.issubset(set(se["pixels"].indices)):
                    valid = True
                    break

        if not valid:
            raise CompileError(
                f"shift at {shift_start}s snapshots {source_anim.anim_type} "
                f"but no matching event ends before shift starts "
                f"with covering pixels"
            )

    # Buffer pool sanity
    for layer in layers:
        for e in layer["events"]:
            if "buffer_id" in e:
                if e["buffer_id"] >= len(buffer_pool):
                    raise CompileError(
                        f"buffer_id {e['buffer_id']} out of range "
                        f"(pool size {len(buffer_pool)})"
                    )


# ---------------------------------------------------------------------------
# 6. Resolve animation params to binary-ready values
# ---------------------------------------------------------------------------

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
        return {
            "channel": CHANNELS[p["channel"]],
            "h": float(p["h"]),
            "s": float(p["s"]),
            "v": float(p["v"]),
            "min_val": float(p["min_val"]),
            "max_val": float(p["max_val"]),
            "period": float(p["period"]),
            "phase0": float(p["phase0"]),
            "pixel_step": float(p["pixel_step"]),
        }
    elif anim_type == "spark":
        color_h, color_s, color_v = _resolve_color(p["color"])
        return {
            "color_h": color_h,
            "color_s": color_s,
            "color_v": color_v,
            "fade": float(p["fade"]),
        }
    elif anim_type == "shift":
        fill_h, fill_s, fill_v = _resolve_color(p.get("fill", "transparent"))
        fill_a = 0.0 if p.get("fill") == "transparent" else 1.0
        init_mode = 0  # CONST
        source_layer = 0
        if "snapshot" in event:
            init_mode = 1  # SNAPSHOT
            source_layer = event.get("_source_layer", 0)
        return {
            "direction": DIRECTIONS[p["direction"]],
            "velocity": float(p["velocity"]),
            "circular": 1 if p.get("circular", False) else 0,
            "fill_h": fill_h,
            "fill_s": fill_s,
            "fill_v": fill_v,
            "fill_a": fill_a,
            "init_mode": init_mode,
            "source_layer": source_layer,
            "buffer_id": event.get("buffer_id", 0),
        }
    elif anim_type == "fill":
        color_h, color_s, color_v = _resolve_color(p["color"])
        return {
            "color_h": color_h,
            "color_s": color_s,
            "color_v": color_v,
        }
    else:
        raise CompileError(f"unknown animation type '{anim_type}'")


# ---------------------------------------------------------------------------
# Resolve snapshot source layers
# ---------------------------------------------------------------------------

def _resolve_snapshot_layers(events: list[dict], layers: list[dict]):
    """For shift events with snapshot, find which layer the source anim is on."""
    anim_to_layer = {}
    for li, layer in enumerate(layers):
        for e in layer["events"]:
            anim_to_layer[id(e["anim"])] = li

    for e in events:
        if "snapshot" in e:
            source_anim = e["snapshot"]
            if id(source_anim) in anim_to_layer:
                e["_source_layer"] = anim_to_layer[id(source_anim)]


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


def _compile_strip(strip_events: list[dict], duration: float) -> bytes:
    """Run the per-strip pipeline (layers → buffers → validation → blob)."""
    # 3. Layer inference
    layers = _infer_layers(strip_events)

    # 4. Buffer packing
    buffer_pool = _pack_buffers(layers)

    # Resolve snapshot source layers (needs layer info)
    _resolve_snapshot_layers(strip_events, layers)

    # 5. Late validation (timing, snapshots)
    _validate_late(strip_events, layers, buffer_pool, duration)

    # 6. Resolve animation params to binary-ready values
    for layer in layers:
        for e in layer["events"]:
            e["binary_params"] = _resolve_anim_params(e)

    # Compute max remap length
    max_remap = max(
        (len(e["index_remap"]) for layer in layers for e in layer["events"]),
        default=0,
    )

    # 7. Blob emission
    return emit_blob(layers, buffer_pool, duration, max_remap)


def compile_program(strips: list[StripDef], events: list[dict],
                    beat: float, duration: float) -> dict[str, bytes]:
    """Full compile pipeline: returns one binary blob per strip."""
    # Deep copy events so we don't mutate the builder's originals
    events = [dict(e) for e in events]

    # 1. Early validation (bounds, params) — across all strips
    _validate_early(events, strips)

    # 2. Time resolution — global, strip-independent
    _resolve_times(events, beat, duration)

    # 3–7. Per-strip: layer inference → buffer packing → validation → blob
    by_strip = _partition_by_strip(events)
    return {
        name: _compile_strip(strip_events, duration)
        for name, strip_events in by_strip.items()
    }
