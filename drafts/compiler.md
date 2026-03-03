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

    def compile(self, beat, duration):
        blob = self._emit(beat, duration)
        self.reset()
        return blob

_builder = _ProgramBuilder()
```

`compile()` at the end of the DSL file triggers the whole pipeline and resets the builder.

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

_(TBD)_

---

## 5. Validation

_(TBD)_

---

## 6. Blob Emission

_(TBD)_
