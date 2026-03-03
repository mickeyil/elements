# Elements v2 — Compiler Design

The compiler takes a DSL program and emits a binary blob. It runs on the base station (PC), not the ESP32. All heavy lifting — time resolution, layer inference, buffer packing — happens here.

## Pipeline

```
DSL (.py)
  → 1. Parser       — DSL calls → structured data
  → 2. Time resolution  — beats/sec → absolute seconds
  → 3. Layer inference  — events → layers (interval graph coloring)
  → 4. Buffer packing   — stateful animations → shared buffer slots
  → 5. Validation       — bounds, references, timing checks
  → 6. Blob emission    — serialize to binary
```

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
    "snapshot": wave1,     # for shift
}
```

**The hidden builder:**

```python
class _ProgramBuilder:
    def reset(self):
        self.strips = []
        self.animations = []
        self.events = []

    def build(self, beat, duration):
        blob = self._emit(beat, duration)
        self.reset()
        return blob

_builder = _ProgramBuilder()
```

`build()` at the end of the DSL file triggers the whole pipeline and resets the builder.

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

    # Validate against program duration
    for e in events:
        if e["end_sec"] > duration:
            raise CompileError(
                f"{e['anim'].anim_type} event ends at {e['end_sec']}s "
                f"but program duration is {duration}s"
            )
```

**Animation params** with time units (e.g. `period`, `fade`) go through the same conversion. The compiler knows which params are time-based per animation type:

```python
TIME_PARAMS = {
    "wave":  ["period"],
    "spark": ["fade"],
    "shift": ["velocity"],  # pixels/beat → pixels/sec
}
```

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
- Only stateful animations participate (currently just shift). Wave, spark, fill are stateless — no buffer.
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

**Output:** a `BufferPool` spec — array of `{id, size}`. Goes into the Program struct. The engine allocates all buffers once at load, passes the right pointer to each animation's `init()` via its `buffer_id`.

---

## 5. Validation

Runs after time resolution, layer inference, and buffer packing — has the full picture. Catches errors that would be silent bugs on the ESP32.

**Checks:**

1. **Pixel bounds** — every index in every pixel group must be `< strip.length`. Error if not.

2. **Event timing vs program duration:**
   - Event *starts* after duration → **error** (dead code, definitely a mistake)
   - Event *extends* past duration → **warning**, clamp `end_sec` to `duration` (song ends when it ends, event just gets cut short — no harm)

3. **Snapshot references** — when shift says `snapshot=wave1`, verify that wave1 has an event that ends `<= shift.at_sec` with a pixel group that covers the shift's pixels. Otherwise shift would snapshot an uninitialized buffer.

   ```python
   def validate_snapshot(event, all_events):
       if "snapshot" not in event:
           return

       source_anim = event["snapshot"]
       shift_start = event["at_sec"]
       shift_pixels = set(event["pixels"].indices)

       source_events = [e for e in all_events if e["anim"] is source_anim]

       if not source_events:
           raise CompileError(
               f"snapshot references {source_anim.anim_type} "
               f"but it has no scheduled events"
           )

       valid = False
       for se in source_events:
           if se["end_sec"] <= shift_start:
               if shift_pixels.issubset(set(se["pixels"].indices)):
                   valid = True
                   break

       if not valid:
           raise CompileError(
               f"shift at {shift_start}s snapshots {source_anim.anim_type} "
               f"but no matching event ends before shift starts "
               f"with covering pixels"
           )
   ```

4. **Animation params completeness** — each animation type has required params. Missing `period` on a wave → compile error, not a runtime mystery.

5. **Layer limit** — already enforced in layer inference (`max_layers=32`), surfaced here with context about which events caused the overflow.

6. **Duplicate strip names** → error.

7. **Empty program** — no strips, no events → error or warning.

8. **Buffer pool sanity** — no `buffer_id` outside pool bounds. Internal assertion.

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
version:          uint8     1
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
anim_type:         uint8     (WAVE=0, SHIFT=1, SPARK=2, FILL=3, ...)
t_start:           float32   (seconds)
duration:          float32   (seconds)
remap_is_identity: uint8     (1 = full layer, skip scatter copy)
remap_length:      uint8     (how many pixels this event writes)
remap:             uint8[remap_length]  (positions in layer buffer)
params_size:       uint8
params:            uint8[params_size]   (animation-specific)
```

The `remap` tells the engine which positions in the layer's HSVA buffer this animation writes to. When a layer has a merged index map (e.g. `[0,4,5,9]` from combining two pixel groups), spark_white targeting `[0,4]` gets remap `[0,1]` and spark_yellow targeting `[5,9]` gets remap `[2,3]`.

Each event includes a `remap_is_identity` flag (uint8) set by the compiler when the remap covers the full layer buffer in order. The engine uses this to skip the scatter copy (see Render Strategy below).

### Animation params (all floats are float32)

**Wave (29 bytes):**
```
channel:    uint8    (H=0, S=1, V=2)
h:          float32
s:          float32
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

**Shift (25 bytes):**
```
direction:    uint8    (LEFT=0, RIGHT=1)
velocity:     float32
circular:     uint8
fill_h:       float32
fill_s:       float32
fill_v:       float32
fill_a:       float32
init_mode:    uint8    (CONST=0, SNAPSHOT=1)
source_layer: uint8    (for SNAPSHOT)
buffer_id:    uint8    (index into buffer pool)
```

### Render strategy — zero allocation during playback

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

No `malloc`/`free` during playback. One temp buffer, reused every frame. Works because the engine processes one animation at a time — never two renders concurrently.

### Test animation blob size estimate

```
Header:                           11 bytes
Buffer pool (1 buffer):            1 byte
Layer 0 (10 indices, 2 events):
  index_map:                      1 + 10 = 11
  event_count:                    2
  wave event:                     1+4+4+1+10+1+29 = 50
  shift event:                    1+4+4+1+10+1+22 = 43
Layer 1 (4 indices, 8 events):
  index_map:                      1 + 4 = 5
  event_count:                    2
  8 spark events:                 8 × (1+4+4+1+2+1+16) = 232
                                  ─────
Total:                            ~357 bytes
```

Fits in a single MQTT message.
