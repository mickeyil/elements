"""Elements v2 compiler — the full pipeline.

Pipeline:
    1. Validation (early) — bounds, params completeness
    2. Time resolution    — beats/sec → absolute seconds
    3. Layer inference     — events → layers (bin-packing with index merging)
    4. Buffer packing     — stateful animations → shared buffer slots
    5. Source resolution   — resolve source= references, compute required_start_sec
    6. Validation (late)  — source references, timing checks
    7. Safe interval analysis — dependency-aware per-strip safe intervals
    8. Param resolution   — resolve animation params to binary-ready values
    9. Blob emission      — serialize to binary
"""

from __future__ import annotations
import colorsys
import math
import warnings
from typing import Any

from .types import (
    SecMarker, AnimDef, PixelGroup, StripDef, COLORS,
    ANIM_TYPES, TIME_PARAMS, REQUIRED_PARAMS, STATEFUL_TYPES,
    CHANNELS, DIRECTIONS,
    CompiledStripArtifact, CompiledManifest,
)
from .blob import emit_blob


class CompileError(Exception):
    pass


SOURCE_NONE = 0xFF


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

def _resolve_times(events: list[dict], beat: float, duration: float):
    """Convert SecMarker seconds to beats, then convert all beat values to seconds."""
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
    """Validate timing and buffer assignment after processing."""
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

def _convert_color_tuple(t: tuple, fmt: str) -> tuple[float, float, float, float]:
    """Convert a 3- or 4-tuple to (H, S, V, A) in HSV. H is 0-360."""
    a = float(t[3]) if len(t) == 4 else 1.0
    if fmt == "rgb":
        r, g, b = float(t[0]) / 255.0, float(t[1]) / 255.0, float(t[2]) / 255.0
        h, s, v = colorsys.rgb_to_hsv(r, g, b)
        return (h * 360.0, s, v, a)
    else:  # hsv
        return (float(t[0]), float(t[1]), float(t[2]), a)


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
        return {
            "direction": DIRECTIONS[p["direction"]],
            "velocity": float(p["velocity"]),
            "circular": 1 if p.get("circular", False) else 0,
            "fill_h": fill_h,
            "fill_s": fill_s,
            "fill_v": fill_v,
            "fill_a": fill_a,
            "buffer_id": event.get("buffer_id", 0),
        }
    elif anim_type == "paint":
        fmt = p.get("format", "hsv")
        if "colors" in p:
            pixels = [_convert_color_tuple(c, fmt) for c in p["colors"]]
            return {"mode": 1, "pixels": pixels}
        else:
            color = p["color"]
            if isinstance(color, str):
                h, s, v = _resolve_color(color)
                return {"mode": 0, "color_h": h, "color_s": s, "color_v": v, "color_a": 1.0}
            else:
                h, s, v, a = _convert_color_tuple(color, fmt)
                return {"mode": 0, "color_h": h, "color_s": s, "color_v": v, "color_a": a}
    else:
        raise CompileError(f"unknown animation type '{anim_type}'")


# ---------------------------------------------------------------------------
# Resolve source-layer dependencies
# ---------------------------------------------------------------------------

def _resolve_and_validate_source_layers(events: list[dict], layers: list[dict]):
    """Resolve event['source'] references into event['source_layer']."""
    anim_to_layers: dict[int, list[int]] = {}
    for li, layer in enumerate(layers):
        for e in layer["events"]:
            anim_to_layers.setdefault(id(e["anim"]), []).append(li)

    event_to_layer = {}
    for li, layer in enumerate(layers):
        for e in layer["events"]:
            event_to_layer[id(e)] = li

    anim_to_events: dict[int, list[dict]] = {}
    for e in events:
        anim_to_events.setdefault(id(e["anim"]), []).append(e)

    for e in events:
        source_anim = e.get("source")
        if source_anim is None:
            e["source_layer"] = SOURCE_NONE
            continue

        if not isinstance(source_anim, AnimDef):
            raise CompileError(
                f"{e['anim'].anim_type} event at {e['at_sec']}s has invalid source "
                f"value {source_anim!r}; expected an AnimDef"
            )

        source_aid = id(source_anim)
        if source_aid not in anim_to_layers:
            raise CompileError(
                f"{e['anim'].anim_type} at {e['at_sec']}s uses source= "
                f"{source_anim.anim_type}, but it has no scheduled events"
            )

        source_layers = sorted(set(anim_to_layers[source_aid]))
        if len(source_layers) > 1:
            raise CompileError(
                f"source AnimDef {source_anim.anim_type} appears on multiple layers "
                f"({', '.join(map(str, source_layers))}); use a separate "
                f"AnimDef per source reference"
            )

        source_li = source_layers[0]
        dep_li = event_to_layer[id(e)]

        source_events = [se for se in anim_to_events[source_aid]
                         if event_to_layer[id(se)] == source_li]
        source_evt = max(
            (se for se in source_events if se["end_sec"] <= e["at_sec"]),
            key=lambda se: se["end_sec"],
            default=None,
        )
        if source_evt is None:
            raise CompileError(
                f"{e['anim'].anim_type} at {e['at_sec']}s uses source= "
                f"{source_anim.anim_type} on layer {source_li}, but no source event "
                f"has ended by the shift start"
            )

        if not set(e["pixels"].indices).issubset(set(source_evt["pixels"].indices)):
            missing = sorted(set(e["pixels"].indices) - set(source_evt["pixels"].indices))
            raise CompileError(
                f"{e['anim'].anim_type} at {e['at_sec']}s uses source= "
                f"{source_anim.anim_type}, but source event (ended at "
                f"{source_evt['end_sec']}s) does not cover pixels {missing}"
            )

        if source_li > dep_li:
            raise CompileError(
                f"{e['anim'].anim_type} on layer {dep_li} declares source= "
                f"{source_anim.anim_type} on layer {source_li}, but source_layer "
                f"must be <= dependent layer"
            )

        e["source_layer"] = source_li
        e["source_event"] = source_evt

        source_end = source_evt["end_sec"]
        dep_start = e["at_sec"]
        for se in layers[source_li]["events"]:
            if se is source_evt or se is e:
                continue
            if se["at_sec"] < dep_start and se["end_sec"] > source_end:
                warnings.warn(
                    f"event on source layer {source_li} between {source_end}s and {dep_start}s "
                    f"may overwrite source buffer before shift starts",
                    stacklevel=2,
                )
                break


# ---------------------------------------------------------------------------
# Safe interval analysis
# ---------------------------------------------------------------------------

def _compute_required_starts(layers: list[dict]):
    """Set required_start_sec on each event, accounting for source dependencies.

    Must process layers in ascending index order. Within each layer, events
    are sorted by at_sec. The source_layer <= dependent_layer invariant
    (enforced by _resolve_and_validate_source_layers) guarantees the source
    event's required_start_sec is already set when the dependent is processed.
    Same-layer dependencies also work because events within a layer are
    non-overlapping and sorted by time.
    """
    for layer in layers:
        for e in layer["events"]:
            if e["source_layer"] == SOURCE_NONE:
                e["required_start_sec"] = e["at_sec"]
            else:
                e["required_start_sec"] = e["source_event"]["required_start_sec"]


def _find_safe_intervals(layers: list[dict], duration: float) -> list[tuple[float, float]]:
    """Find time intervals where jumping is safe, accounting for source dependencies.

    An event's unsafe span is [required_start_sec, end_sec). For events with
    source dependencies, required_start_sec extends back to include the source
    chain's start time. This is conservative but correct.
    """
    # Build unsafe spans per event
    unsafe = [
        (e["required_start_sec"], e["at_sec"] + e["duration_sec"])
        for layer in layers for e in layer["events"]
    ]
    if not unsafe:
        return [(0.0, duration)]

    # Merge overlapping unsafe spans
    unsafe.sort()
    merged = [list(unsafe[0])]
    for lo, hi in unsafe[1:]:
        if lo <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], hi)
        else:
            merged.append([lo, hi])

    # Complement: gaps between merged unsafe spans
    safe = []
    prev_end = 0.0
    for lo, hi in merged:
        if lo > prev_end:
            safe.append((prev_end, lo))
        prev_end = max(prev_end, hi)
    if prev_end < duration:
        safe.append((prev_end, duration))

    # t=0 is always safe (engine reset at t=0 is correct by construction)
    if not safe or safe[0][0] > 0.0:
        safe.insert(0, (0.0, 0.0))

    return safe


def _intersect_intervals(
    a: list[tuple[float, float]], b: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """Intersect two sorted lists of non-overlapping intervals."""
    result = []
    i = j = 0
    while i < len(a) and j < len(b):
        lo = max(a[i][0], b[j][0])
        hi = min(a[i][1], b[j][1])
        if lo < hi or (lo == hi == 0.0):  # preserve degenerate (0,0)
            result.append((lo, hi))
        if a[i][1] <= b[j][1]:
            i += 1
        else:
            j += 1
    return result


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


def _compile_strip(strip_events: list[dict],
                    duration: float) -> tuple[bytes, list[tuple[float, float]]]:
    """Run the per-strip pipeline. Returns (blob, safe_intervals)."""
    # 3. Layer inference
    layers = _infer_layers(strip_events)

    # 4. Buffer packing
    buffer_pool = _pack_buffers(layers)

    # 5. Source resolution + required_start_sec
    _resolve_and_validate_source_layers(strip_events, layers)

    # 6. Late validation (timing, dependencies)
    _validate_late(strip_events, layers, buffer_pool, duration)

    # 7. Safe interval analysis (after late validation, which may clamp end_sec)
    _compute_required_starts(layers)
    safe_intervals = _find_safe_intervals(layers, duration)

    # 8. Resolve animation params to binary-ready values
    for layer in layers:
        for e in layer["events"]:
            e["binary_params"] = _resolve_anim_params(e)

    # Compute max remap length
    max_remap = max(
        (len(e["index_remap"]) for layer in layers for e in layer["events"]),
        default=0,
    )

    # 9. Blob emission
    return emit_blob(layers, buffer_pool, duration, max_remap), safe_intervals


def compile_manifest(strips: list[StripDef], events: list[dict],
                     beat: float, duration: float) -> CompiledManifest:
    """Full compile pipeline: returns manifest with blobs + safe intervals."""
    # Deep copy events so we don't mutate the builder's originals
    events = [dict(e) for e in events]

    # 1. Early validation (bounds, params) — across all strips
    _validate_early(events, strips)

    # 2. Time resolution — global, strip-independent
    _resolve_times(events, beat, duration)

    # 3–9. Per-strip pipeline; iterate input strips for canonical order.
    # Strip names are unique (enforced in _validate_early), so keying the
    # artifacts by strip_id never collides; insertion order stays canonical.
    by_strip = _partition_by_strip(events)
    strip_artifacts = {}
    per_strip_intervals = []
    for s in strips:
        strip_events = by_strip.get(s.name, [])
        blob, intervals = _compile_strip(strip_events, duration)
        strip_artifacts[s.name] = CompiledStripArtifact(
            strip_id=s.name, length=s.length, blob=blob,
        )
        per_strip_intervals.append(intervals)

    # Global safe interval intersection
    if per_strip_intervals:
        safe = per_strip_intervals[0]
        for si in per_strip_intervals[1:]:
            safe = _intersect_intervals(safe, si)
    else:
        safe = [(0.0, duration)]

    return CompiledManifest(
        duration=duration,
        strips=strip_artifacts,
        safe_intervals=safe,
    )


def compile_program(strips: list[StripDef], events: list[dict],
                    beat: float, duration: float) -> dict[str, bytes]:
    """Full compile pipeline: returns one binary blob per strip."""
    manifest = compile_manifest(strips, events, beat, duration)
    return {a.strip_id: a.blob for a in manifest.strips.values()}
