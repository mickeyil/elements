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

Goal: assign events to layers, minimizing layer count, such that no two events on the same layer overlap in time.

**Constraint:** two events can share a layer only if:
1. Same pixel group (identical index list)
2. Non-overlapping in time (`e1.end_sec <= e2.at_sec`)

**Algorithm — greedy interval assignment:**

```python
from collections import defaultdict

def infer_layers(events):
    # Group by pixel group
    groups = defaultdict(list)
    for e in events:
        key = tuple(e["pixels"].indices)  # hashable
        groups[key].append(e)

    layers = []
    for indices_key, group_events in groups.items():
        group_events.sort(key=lambda e: e["at_sec"])

        # Greedy: try to fit into existing slot, else open new one
        slots = []  # each slot: list of events
        for e in group_events:
            placed = False
            for slot in slots:
                if e["at_sec"] >= slot[-1]["end_sec"]:  # no overlap
                    slot.append(e)
                    placed = True
                    break
            if not placed:
                slots.append([e])

        for slot in slots:
            layers.append({
                "index_map": list(indices_key),
                "events": slot,
            })

    return layers
```

**Test animation result — 3 layers:**

```
Layer 0: index_map=[0..9]  events=[wave(0.0-1.0), shift(1.0-2.0)]
Layer 1: index_map=[0,4]   events=[spark_w(0.0-0.1), spark_w(0.5-0.6), ...]
Layer 2: index_map=[5,9]   events=[spark_y(0.25-0.35), spark_y(0.75-0.85), ...]
```

This maps directly to the C++ `Layer` + `LayerEvents` structs. Layer index = priority in the compositor (layer 0 = bottom).

**Overlapping events on same pixel group** are placed on separate layers — the compositor blends them during the overlap period. This is valid and intentional (e.g. wave fading into shift with a crossfade overlap).

---

## 4. Buffer Packing

_(TBD)_

---

## 5. Validation

_(TBD)_

---

## 6. Blob Emission

_(TBD)_
