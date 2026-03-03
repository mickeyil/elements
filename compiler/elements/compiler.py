"""Elements v2 compiler — the full pipeline.

Pipeline:
    1. Time resolution   — beats/sec → absolute seconds
    2. Layer inference    — events → layers (bin-packing with index merging)
    3. Buffer packing    — stateful animations → shared buffer slots
    4. Validation        — bounds, references, timing checks
    5. Blob emission     — serialize to binary
"""

from __future__ import annotations
import warnings
from typing import Any

from .types import SecMarker, AnimDef, PixelGroup, StripDef, COLORS
from .blob import emit_blob


# ---------------------------------------------------------------------------
# Animation type constants
# ---------------------------------------------------------------------------

ANIM_TYPES = {"wave": 0, "shift": 1, "spark": 2, "fill": 3}

# Which animation params are time-based (beats → seconds)
TIME_PARAMS = {
    "wave":  ["period"],
    "spark": ["fade"],
    "shift": [],  # velocity is pixels/beat → pixels/sec, handled specially
}

# Which animation types need work buffers
STATEFUL_TYPES = {"shift"}

# Channel name → uint8
CHANNELS = {"H": 0, "S": 1, "V": 2}

# Direction name → uint8
DIRECTIONS = {"left": 0, "right": 1}


class CompileError(Exception):
    pass


# ---------------------------------------------------------------------------
# 1. Time resolution
# ---------------------------------------------------------------------------

def _resolve_times(events: list[dict], beat: float, duration: float):
    """Resolve SecMarkers to beats, then convert all times to seconds."""
    for e in events:
        # Resolve SecMarkers to beats
        if isinstance(e["at"], SecMarker):
            e["at"] = e["at"].seconds / beat
        if isinstance(e["duration"], SecMarker):
            e["duration"] = e["duration"].seconds / beat

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
# 2. Layer inference — bin-packing with index map merging
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

    return layers


# ---------------------------------------------------------------------------
# 3. Buffer packing
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
# 4. Validation
# ---------------------------------------------------------------------------

def _validate(events: list[dict], layers: list[dict], strips: list[StripDef],
              buffer_pool: list[dict], duration: float):
    """Run all validation checks."""
    strip_map = {s.name: s for s in strips}

    # Check for duplicate strip names
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

        # Pixel bounds
        if px.strip_name not in strip_map:
            raise CompileError(f"unknown strip '{px.strip_name}'")
        strip_len = strip_map[px.strip_name].length
        for idx in px.indices:
            if idx < 0 or idx >= strip_len:
                raise CompileError(
                    f"pixel index {idx} out of bounds for strip "
                    f"'{px.strip_name}' (length {strip_len})"
                )

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

    # Layer limit already enforced in _infer_layers
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
# 5. Resolve animation params to binary-ready values
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
        # Determine init_mode and source_layer
        init_mode = 0  # CONST
        source_layer = 0
        if "snapshot" in event:
            init_mode = 1  # SNAPSHOT
            # source_layer resolved after layer inference
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
    # Build anim → layer index mapping (using the last event of that anim)
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

def compile_program(strips: list[StripDef], events: list[dict],
                    beat: float, duration: float) -> bytes:
    """Full compile pipeline: returns binary blob."""
    # Deep copy events so we don't mutate the builder's originals
    events = [dict(e) for e in events]

    # 1. Time resolution
    _resolve_times(events, beat, duration)

    # 2. Layer inference
    layers = _infer_layers(events)

    # 3. Buffer packing
    buffer_pool = _pack_buffers(layers)

    # Resolve snapshot source layers (needs layer info)
    _resolve_snapshot_layers(events, layers)

    # 4. Validation
    _validate(events, layers, strips, buffer_pool, duration)

    # 5. Resolve animation params to binary-ready values
    for layer in layers:
        for e in layer["events"]:
            e["binary_params"] = _resolve_anim_params(e)

    # Compute max remap length
    max_remap = 0
    for layer in layers:
        for e in layer["events"]:
            max_remap = max(max_remap, len(e["index_remap"]))

    # 6. Blob emission
    return emit_blob(layers, buffer_pool, duration, max_remap)
