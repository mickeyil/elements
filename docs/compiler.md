# Elements — Compiler Design

> **Status: Implemented.** Pipeline steps match current code in `compiler/elements/`. Some internals below have been updated to reflect the current implementation.

The compiler takes a DSL program and emits one binary blob per strip. It runs on the base station (PC), not the ESP32. All heavy lifting — time resolution, layer inference, buffer packing — happens here.

## Pipeline

```
DSL (.py)
  → 1. Parser               — DSL calls → structured data
  → 2. Validation (early)   — bounds, params completeness, strip names, paint args
  → 3. Time resolution      — beats/sec → absolute seconds (global, all strips)
  → [per strip:]
  → 4. Layer inference       — events → layers (bin-packing with index merging)
  → 5. Buffer packing        — stateful animations → shared buffer slots
  → 6. Source resolution     — resolve source= refs, compute required_start_sec
  → 7. Validation (late)     — source references, timing vs duration, buffer sanity
  → 8. Safe interval analysis — dependency-aware per-strip safe intervals
  → 9. Param resolution      — resolve animation params to binary-ready values
  → 10. Blob emission        — serialize to binary
  → [across strips:]
  → 11. Global safe interval intersection
  → CompiledManifest (per-strip blobs + safe intervals + global safe intervals)
```

Steps 1–3 run once across all events. Steps 4–10 run independently per strip. Step 11 intersects per-strip safe intervals. `build_manifest()` returns `CompiledManifest`; `build()` returns `dict[str, bytes]` for backwards compatibility. Safe intervals are metadata (not embedded in the blob). Events are partitioned by `strip_name` as part of the per-strip loop — there is no separate strip-partition pass.

### Multi-strip

One blob per physical strip/ESP32. Each blob is self-contained — the ESP doesn't know about other strips. The base station sends each blob to its respective device. Since clocks are synced and all devices start at the same `t_program`, they play in sync.

Events are partitioned by `strip_name` before layer inference, so strips never share layers. Two events on different strips with the same pixel indices are not merged — each goes into its own blob.

**Cross-strip stateful animations** (e.g., shift spanning two strips) are a compile error — shift needs a complete snapshot of its source pixels, which can't be split across two independent blobs. Cross-strip stateless animations (wave, spark, paint) splitting is a future feature; for now, schedule each strip independently.

---

## 1. Parser

DSL calls accumulate data into a hidden global `_ProgramBuilder`. The user sees free functions (`strip()`, `wave()`, `.schedule()`); under the hood, each call creates a data object and registers it.

**Data structures:**

```python
@dataclass
class StripDef:
    name: str
    length: int
    type: str       # "RGB"

    def pixels(self, indices_str):
        indices = _parse_indices(indices_str)  # "0-9" → [0..9], "0,4" → [0,4]
        return PixelGroup(self.name, indices)

@dataclass
class PixelGroup:
    strip_name: str
    indices: list[int]

@dataclass
class AnimDef:
    anim_type: str   # "wave", "shift", "spark", ...
    params: dict

    def schedule(self, pixels, at, duration, **kw):
        _builder.add_event(self, pixels, at, duration, **kw)
```

An event in the builder looks like:

```python
{
    "anim": AnimDef("wave", {channel: "V", h: 220, ...}),
    "pixels": PixelGroup("main", [0,1,...,9]),
    "at": 0,               # raw from DSL (beats or SecMarker)
    "duration": 2,         # raw from DSL (beats or SecMarker)
    # optional:
    "source": wave1,       # layer dependency (e.g. shift reads source layer's buffer)
}
```

**The hidden builder:**

```python
class _ProgramBuilder:
    def reset(self):
        self.strips = []
        self.animations = []
        self.events = []
        self.configured_strip_lengths = {}  # strip_name → length from controller config

    # add_strip(), add_animation(), add_event() accumulate data

_builder = _ProgramBuilder()
```

`_ProgramBuilder` accumulates DSL data but does not have its own `build()` method. The public entry points are module-level functions:

- **`build(beat, duration)`** — compiles and returns `dict[str, bytes]` (blob-only, backwards compat)
- **`build_manifest(beat, duration)`** — compiles and returns `CompiledManifest` (blobs + duration + safe intervals)

Both call the internal `compile_program()` / `compile_manifest()` pipeline, then reset the builder.

---

## 2. Time Resolution

All DSL times are in beats by default. `sec(x)` is a lazy marker resolved here.

**`sec()` is a lazy object:**

```python
class SecMarker:
    def __init__(self, seconds):
        self.seconds = seconds
```

Not evaluated at DSL parse time — just stored. Resolved during compilation when `beat` is known.

**Resolution pass:**

```python
def resolve_times(events, beat, duration):
    for e in events:
        # Resolve SecMarkers to beats
        if isinstance(e["at"], SecMarker):
            e["at"] = e["at"].seconds / beat
        if isinstance(e["duration"], SecMarker):
            e["duration"] = e["duration"].seconds / beat

        # Convert beats → seconds
        e["at_sec"]       = e["at"] * beat
        e["duration_sec"] = e["duration"] * beat
        e["end_sec"]      = e["at_sec"] + e["duration_sec"]
```

Time resolution is pure normalization — it converts units but does not validate timing against program duration. Duration checks happen later in late validation (see § 7).

**Animation params** with time units (e.g. `period`, `fade`) go through the same conversion. The compiler knows which params are time-based per animation type:

```python
TIME_PARAMS = {
    "wave":  ["period"],
    "spark": ["fade"],
    "shift": [],  # velocity handled separately
}
```

Shift `velocity` is not a time param — it is pixels/beat, not a duration. The compiler converts it separately: `velocity_pps = velocity / beat` (pixels/beat ÷ seconds/beat = pixels/sec). `SecMarker` velocities are passed through as-is (already in pixels/sec).

**Example** — test animation with `beat=0.5`, `duration=2.0`:

| Event | at (beats) | duration (beats) | at_sec | dur_sec | end_sec |
|-------|-----------|-----------------|--------|---------|---------|
| wave | 0 | 2 | 0.0 | 1.0 | 1.0 |
| shift | 2 | 2 | 1.0 | 1.0 | 2.0 |
| spark_w @beat 0 | 0 | sec(0.1)→0.2 | 0.0 | 0.1 | 0.1 |
| spark_w @beat 1 | 1 | 0.2 | 0.5 | 0.1 | 0.6 |
| spark_y @beat 0.5 | 0.5 | 0.2 | 0.25 | 0.1 | 0.35 |
| spark_y @beat 1.5 | 1.5 | 0.2 | 0.75 | 0.1 | 0.85 |

After this pass, beats are gone. Everything downstream works in seconds.

---

## 3. Layer Inference

Goal: assign events to layers, minimizing layer count, such that no two events on the same layer overlap in time. Max 32 layers (`uint32_t` bitmask in the engine).

**Constraint:** two events can share a layer if:
1. Non-overlapping in time
2. Pixel groups can differ — the layer's index map becomes the union of all its events' pixel groups

This means events with different pixel groups can share a layer when their times don't overlap. The compiler merges index maps and stores a per-event remap so each animation knows which logical buffer positions to write to.

**Algorithm — greedy bin-packing with index map merging:**

```python
def infer_layers(events, max_layers=32):
    events.sort(key=lambda e: e["at_sec"])
    layers = []

    for e in events:
        best = None
        best_size = float('inf')

        for layer in layers:
            # Check time overlap
            if any(overlaps(e, existing) for existing in layer["events"]):
                continue

            # Prefer smallest merged index map
            merged = set(layer["indices"]) | set(e["pixels"].indices)
            if len(merged) < best_size:
                best = layer
                best_size = len(merged)

        if best:
            best["indices"] = sorted(set(best["indices"]) | set(e["pixels"].indices))
            best["events"].append(e)
            # Store per-event remap: logical index in merged map
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

    return layers


def overlaps(a, b):
    return a["at_sec"] < b["end_sec"] and b["at_sec"] < a["end_sec"]
```

**Walkthrough — test animation:**

Events sorted by start time:

```
wave       [0-9]  0.0-1.0  → new layer 0, indices=[0..9]
spark_w    [0,4]  0.0-0.1  → overlaps wave on L0 → new layer 1, indices=[0,4]
spark_y    [5,9]  0.25-0.35 → L0: overlaps wave → no
                             → L1: 0.25 >= 0.1 ✓, merge → indices=[0,4,5,9]
spark_w    [0,4]  0.5-0.6  → L0: overlaps wave → no
                             → L1: 0.5 >= 0.35 ✓ → fits
spark_y    [5,9]  0.75-0.85 → L0: overlaps wave → no
                             → L1: 0.75 >= 0.6 ✓ → fits
shift      [0-9]  1.0-2.0  → L0: 1.0 >= 1.0 ✓ → fits
spark_w    [0,4]  1.0-1.1  → L0: overlaps shift → no
                             → L1: 1.0 >= 0.85 ✓ → fits
... (remaining sparks fit in L1)
```

**Result: 2 layers instead of 3.**

```
Layer 0: index_map=[0..9]      events=[wave(0.0-1.0), shift(1.0-2.0)]
Layer 1: index_map=[0,4,5,9]   events=[all sparks interleaved]
```

Per-event index remap for layer 1 (`[0,4,5,9]`):
- spark_white targets `[0,4]` → remap `[0,1]`
- spark_yellow targets `[5,9]` → remap `[2,3]`

The remap tells the engine which positions in the layer's HSVA buffer each animation writes to.

**Overlapping events** that can't fit in any existing layer get a new layer. The compositor blends them during the overlap period (e.g. wave fading into shift with a crossfade).

---

## 4. Buffer Packing

Stateful animations (like shift) need work buffers for init data (e.g. snapshot of another layer's pixels). The compiler knows which events need buffers, how big, and when they're active — so non-overlapping stateful events can share a buffer slot.

**Key points:**
- Independent of layer assignment — packing is purely about time overlaps between stateful events
- Buffer size = number of pixels the animation writes to (`len(event.pixels.indices)`), not the full layer size
- Only stateful animations participate (currently just shift). Wave, spark, paint are stateless — no buffer.
- Non-overlapping stateful events can share a slot. The buffer contents are meaningless between usages — the next animation overwrites entirely at init.

**Algorithm — greedy bin-packing by time:**

```python
def pack_buffers(layers):
    # Collect all events that need work buffers
    stateful = []
    for layer in layers:
        for e in layer["events"]:
            if needs_buffer(e["anim"].anim_type):
                stateful.append({
                    "event": e,
                    "at_sec": e["at_sec"],
                    "end_sec": e["end_sec"],
                    "size": len(e["pixels"].indices),
                })

    if not stateful:
        return []  # no buffers needed

    stateful.sort(key=lambda s: s["at_sec"])
    slots = []

    for s in stateful:
        placed = False
        for slot in slots:
            if s["at_sec"] >= slot["end"]:  # no overlap
                slot["end"] = s["end_sec"]
                slot["size"] = max(slot["size"], s["size"])  # grow if needed
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
```

**Test animation walkthrough:**

Only one stateful event: shift (1.0-2.0s, 10 pixels).
- → 1 buffer slot, size 10
- `shift.buffer_id = 0`

ESP32 allocates one `hsva_t[10]` at program load. Done.

**Complex example — overlapping stateful events:**

```
shift_a  [0..9]   0.0-2.0s  (10 pixels)
shift_b  [0..4]   1.0-1.5s  (5 pixels)   ← overlaps shift_a
shift_c  [5..9]   2.5-3.0s  (5 pixels)
```

- shift_a → slot 0 (size 10, end 2.0)
- shift_b → slot 0: `1.0 >= 2.0`? No → new slot 1 (size 5, end 1.5)
- shift_c → slot 0: `2.5 >= 2.0`? ✓ → reuse slot 0, size stays 10

Result: 2 buffers `[10, 5]`. shift_a and shift_c share slot 0, shift_b gets slot 1. ESP32 allocates `hsva_t[10]` + `hsva_t[5]` at load time.

**Output:** a `BufferPool` spec — array of `{id, size}`. Goes into the Program struct. The decoder allocates all buffers at decode time. The engine's factory function looks up the buffer via `buffer_id` and pre-fills it with source pixels before passing it to the animation's constructor.

---

## 5. Validation

Validation is split into two phases: **early** (before layer inference, on raw events) and **late** (after source resolution, with the full picture).

### Early validation (before time resolution)

Runs on raw DSL events. Catches structural errors before any compilation work.

1. **Empty program** — no events → hard compile error (`empty program: no events`).
2. **Duplicate strip names** → error.
3. **Pixel bounds** — every index in every pixel group must be `< strip.length`. Error if not.
4. **Animation params completeness** — each animation type has required params. Missing `period` on a wave → compile error, not a runtime mystery.
5. **Paint-specific argument validation** — paint color arguments are validated for correct structure (solid color or per-pixel array).
6. **Unknown event-level options** — unrecognized keyword arguments passed to `.schedule()` are rejected.

### Late validation (after source resolution)

Runs after layer inference, buffer packing, and source resolution — has the full picture.

7. **Event timing vs program duration:**
   - Event *starts* after duration → **error** (dead code, definitely a mistake)
   - Event *extends* past duration → **warning**, clamp `end_sec` to `duration` (song ends when it ends, event just gets cut short — no harm)

8. **Source layer ordering** — for each event with a `source` field, verify that the source animation's layer index is `<= dependent_layer`. Same-layer dependencies are valid because events within a layer are non-overlapping and sorted by time.

9. **Invalid source values** — source must reference a scheduled `AnimDef`. Unscheduled source animations are rejected.

10. **Source on multiple layers** — a source `AnimDef`'s events must all land on a single layer. If the compiler placed them on different layers, the source reference is ambiguous → error.

11. **Source event must have ended before dependent starts** — the source event's `end_sec` must be `<= dependent event's at_sec`. Otherwise the dependent would snapshot a partially-rendered buffer.

12. **Dependent pixels subset of source** — the dependent event's pixel group must be a subset of the source event's pixel group. This ensures the snapshot covers all needed pixels.

13. **Buffer clobber warning** — fires when another event on the source layer overlaps the interval between `source_end` and dependent start, meaning it may overwrite the source buffer before the shift snapshots it.

   ```python
   def warn_buffer_clobber(event, source_layer_events):
       if "source" not in event:
           return
       source_end = find_source_end_sec(event)
       dep_start = event["at_sec"]
       for se in source_layer_events:
           if se["anim"] is event["source"]:
               continue  # skip the source event itself
           if se["at_sec"] < dep_start and se["end_sec"] > source_end:
               warn(f"event on source layer overlaps [{source_end}s, "
                    f"{dep_start}s) — buffer may be overwritten before snapshot")
   ```

14. **Buffer pool sanity** — no `buffer_id` outside pool bounds. Internal assertion.

15. **Layer limit** — already enforced in layer inference (`max_layers=32`), surfaced here with context about which events caused the overflow.

---

## 6. Blob Emission

The blob is the binary format the C++ decoder reads to reconstruct a `Program` struct. Design goals: simple to decode on ESP32, no parsing overhead, little-endian (ESP32 native), single linear pass.

### Overall structure

```
┌─────────────────────────┐
│ Header                  │  magic + version + metadata
├─────────────────────────┤
│ Buffer Pool             │  sizes for pre-allocation
├─────────────────────────┤
│ Layer 0                 │  index map + events
│   Event 0              │    type + timing + remap + params
│   Event 1              │
│   ...                  │
├─────────────────────────┤
│ Layer 1                 │
│   ...                  │
└─────────────────────────┘
```

### Header (12 bytes)

```
magic:            4 bytes   "ELEM"
version:          uint8     2
layer_count:      uint8
buffer_count:     uint8
max_remap_length: uint8     (for pre-allocating temp render buffer)
duration:         float32   (seconds)
```

### Buffer pool (1 byte per buffer)

```
for each buffer:
    size: uint8   (pixel count)
```

### Layer

```
index_map_length: uint8
index_map:        uint8[index_map_length]   (physical strip indices)
event_count:      uint16
events:           Event[event_count]
```

### Event

```
anim_type:         uint8     (WAVE=0, SHIFT=1, SPARK=2, PAINT=3)
t_start:           float32   (seconds)
duration:          float32   (seconds)
source_layer:      uint8     (0xFF = none)
remap_is_identity: uint8     (1 = full layer, skip scatter copy)
remap_length:      uint8     (how many pixels this event writes)
remap:             uint8[remap_length]  (positions in layer buffer)
params_size:       uint8
params:            uint8[params_size]   (animation-specific)
```

The `remap` tells the engine which positions in the layer's HSVA buffer this animation writes to. When a layer has a merged index map (e.g. `[0,4,5,9]` from combining two pixel groups), spark_white targeting `[0,4]` gets remap `[0,1]` and spark_yellow targeting `[5,9]` gets remap `[2,3]`.

Each event includes a `remap_is_identity` flag (uint8) set by the compiler when the remap covers the full layer buffer in order. The engine uses this to skip the scatter copy (see Render Strategy below).

### Animation params (all floats are float32)

**Wave (33 bytes):**
```
channel:    uint8    (H=0, S=1, V=2)
h:          float32
s:          float32
v:          float32
min_val:    float32
max_val:    float32
period:     float32
phase0:     float32
pixel_step: float32
```

**Spark (16 bytes):**
```
color_h:  float32
color_s:  float32
color_v:  float32
fade:     float32
```

**Shift (23 bytes):**
```
direction:    uint8    (LEFT=0, RIGHT=1)
velocity:     float32
circular:     uint8
fill_h:       float32
fill_s:       float32
fill_v:       float32
fill_a:       float32
buffer_id:    uint8    (index into buffer pool)
```

**Paint — solid (17 bytes):**
```
mode:       uint8    (0 = solid)
color_h:    float32
color_s:    float32
color_v:    float32
color_a:    float32
```

**Paint — per-pixel (2 + N*16 bytes):**
```
mode:         uint8    (1 = per_pixel)
pixel_count:  uint8
pixels:       [h: float32, s: float32, v: float32, a: float32] × pixel_count
```

Note: `source_layer` is in the event header, not in shift params. `init_mode`
has been removed — the animation decides internally whether to freeze or
live-read the source buffer.

### Render strategy — near-zero allocation during playback

The engine pre-allocates a single temp buffer at program load, sized to `max_remap_length` (from header). At render time:

```cpp
if (event.remap_is_identity) {
    // Full layer — render directly into layer buffer
    anim->render(layer->buffer(), layer->length(), t_rel);
} else {
    // Partial layer — render to temp, scatter copy
    anim->render(_temp, event.remap_length, t_rel);
    for (uint8_t i = 0; i < event.remap_length; i++)
        layer->buffer()[event.remap[i]] = _temp[i];
}
```

Buffers are pre-allocated — no per-frame allocation. Animation instances are `new`/`delete` per event activation (once per transition, not per frame). One temp buffer, reused every frame. Works because the engine processes one animation at a time — never two renders concurrently.

### Test animation blob size estimate

```
Header:                           12 bytes
Buffer pool (1 buffer):            1 byte
Layer 0 (10 indices, 2 events):
  index_map_length + map:         1 + 10 = 11
  event_count (u16):              2
  wave event:                     1+4+4+1+1+1+10+1+33 = 56
  shift event:                    1+4+4+1+1+1+10+1+23 = 46
Layer 1 (4 indices, 8 events):
  index_map_length + map:         1 + 4 = 5
  event_count (u16):              2
  8 spark events:                 8 × (1+4+4+1+1+1+2+1+16) = 248
                                  ─────
Total:                            383 bytes
```

Note: LOAD is sent over TCP (not UDP), so the blob size is not constrained by MTU.

---

## 8. Safe Interval Analysis

After late validation, the compiler analyzes the per-strip event timeline to find **safe intervals** — time ranges where `Engine::reset()` followed by `tick(t)` produces correct output without replaying from t=0. This enables cheap seek on real ESP hardware.

### Why intervals, not points

A gap between events is a continuous range `[gap_start, gap_end)`, not a single timestamp. Modeling safe regions as intervals is essential for correct multi-strip intersection. If strip A is safe on `[4.0, 5.0)` and strip B is safe on `[4.5, 6.0)`, the global safe region is `[4.5, 5.0)`. A point-based model would record `4.0` for A and `4.5` for B — set intersection yields nothing, missing the valid synchronized jump window.

### Dependency-aware unsafe spans

A naive "no event active on any layer" rule is insufficient when source dependencies exist. Consider:

```
paint:              [0.0, 1.0)
shift(source=paint): [2.0, 4.0)
```

The gap `[1.0, 2.0)` appears safe — no event is active. But jumping to `t=1.5` loses paint's buffer state: after `Engine::reset()`, all layer buffers are zeroed. When shift constructs at `t=2.0`, it snapshots zeroes instead of paint's output. The gap is actually unsafe.

The correct rule: each event's **unsafe span** extends back to the start of its source dependency chain.

### Algorithm

**Step 1 — `required_start_sec`:** For each event, compute the earliest time from which playback must begin to produce correct output:

- No source dependency: `required_start_sec = at_sec`
- Source dependency: `required_start_sec = source_event.required_start_sec`

This propagates transitively: if A feeds B and B feeds C, then C's `required_start_sec` is A's. Processing layers in ascending index order (guaranteed by the `source_layer <= dependent_layer` invariant) ensures the source event is already computed.

**Step 2 — merge and complement:** Collect all unsafe spans `[required_start_sec, end_sec)` across all layers, merge overlaps, and take the complement over `[0, duration]`:

```python
def _find_safe_intervals(layers, duration):
    unsafe = [
        (e["required_start_sec"], e["at_sec"] + e["duration_sec"])
        for layer in layers for e in layer["events"]
    ]
    # sort, merge overlaps, compute complement → safe intervals
```

**Step 3 — degenerate t=0:** `Engine::reset()` at t=0 is always correct by construction. If the first unsafe span starts at 0.0, the degenerate interval `(0.0, 0.0)` is inserted to represent this.

### Global safe intervals

The compiler computes per-strip safe intervals internally, then intersects them across all strips. Only the global result is exposed on `CompiledManifest.safe_intervals` — this is the set of times where all devices can safely jump simultaneously. Per-strip intervals are compile-time intermediates, not part of the public surface. Eventless strips receive a valid zero-layer blob and a safe interval of `[(0.0, duration)]`, which is the identity for intersection.

### Output

Safe intervals are returned as metadata in `CompiledManifest`, not embedded in the blob. The blob format and C++ decoder are unchanged.

```python
manifest = build_manifest(beat=1.0, duration=15.0)
manifest.safe_intervals  # [(0.0, 0.0), (4.5, 5.0), (9.0, 15.0)]
```

`build()` and `compile_program()` continue to return `dict[str, bytes]` for backwards compatibility.

### Example walkthrough

```
paint:               [0.0, 1.0)   required_start = 0.0
shift(source=paint): [2.0, 4.0)   required_start = 0.0  (follows paint)
```

Unsafe spans: `[0.0, 1.0)` and `[0.0, 4.0)`. Merged: `[0.0, 4.0)`. For duration=5.0, safe intervals: `[(0.0, 0.0), (4.0, 5.0)]`.
