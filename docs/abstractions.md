# Elements v2 — Abstractions

This document defines the core abstractions for the v2 animation engine. It is the result of iterative design discussion and supersedes the "Rendering Abstractions" and "Animation System" sections in [v2_design.md](v2_design.md) where they conflict.

---

## Overview

The ESP32 is a dumb playback engine. All heavy lifting — analysis, scheduling, memory planning — happens on the base station, which compiles a **Program** and sends it to the device. The device loads the program, pre-allocates buffers, and plays it back. Animation instances are `new`/`delete` per event activation (near-zero allocation; impact to be measured with a full song).

The rendering pipeline:

```
Program (static data)
  → Engine (scans per-layer timelines, manages animation lifecycle)
    → Animations (render into layer buffers)
      → Compositor (blends active layers into strip, bottom to top)
        → Strip (physical LED output)
```

---

## Strip

The physical LED output buffer. A thin wrapper around FastLED's CRGB array (or a plain RGB buffer for the PC simulator).

- One Strip instance per device
- Length = total number of physical LEDs
- The compositor blends directly into this buffer — no separate composite buffer

Unchanged from the current implementation.

---

## Layer

A layer is a pixel buffer with a mapping to physical LED indices. It combines three concerns:

1. **Pixel group** — which physical LEDs this layer addresses (index mapping)
2. **Buffer** — HSVA values that animations write into
3. **Priority** — blending order in the compositor (implicit: array index = priority)

**Properties:**
- **Length** — number of logical pixels
- **Index mapping** — array of `logical_index → physical_strip_index`
- **HSVA buffer** — sized to length, not full strip. H, S, V, A are `float`.

**Key design decisions:**

- **No ID field.** A layer's position in the program's layer array is its identity and its priority. Layer 0 is the bottom (rendered first), layer N-1 is the top.
- **No animation pointer.** The layer doesn't know what writes into it. The engine decides which animation renders into which layer based on the timeline. The layer is purely passive.
- **No clear per frame.** If a layer has no active animation event, the compositor skips it. If it has an active event, the animation overwrites the buffer. No need to zero the buffer every frame.
- **Buffer survives animation end.** When an animation finishes, the layer's buffer retains the last rendered values. A subsequent animation can read this data via a source layer dependency (e.g., shift snapshots the source layer's buffer). The buffer is not cleared between events — ownership passes to the next animation.
- **Layers are static.** All layers are defined at program load and live for the entire program. No creation or destruction during playback. The compiler determines the required layers; the engine allocates them once.
- **Layers may overlap in physical indices.** A background layer on [0..49] and a spark layer on [4,11,21,25] both address physical LEDs 4, 11, 21, and 25. The compositor resolves this via priority ordering and alpha blending.
- **Per-pixel alpha.** Alpha is in the HSVA buffer per pixel, not per layer. This enables effects like cascading sparks where each pixel fades independently.

**Analogy:** Layers are like tracks in a DAW. Bottom layers are long-running backgrounds. Upper layers are sparse, short-lived effects aligned to beats or other events. The compiler packs animation events into layers such that no two events on the same layer overlap in time.

```cpp
class Layer {
public:
    Layer(const uint8_t* index_map, uint8_t length);
    ~Layer();

    uint8_t length() const;
    const uint8_t* index_map() const;
    hsva_t* buffer();

private:
    uint8_t  _length;
    uint8_t* _index_map;
    hsva_t*  _buffer;
};
```

---

## Animation

An animation is an instance that renders pixel values into a layer's HSVA buffer. Animations are created when an event activates and destroyed when it ends.

**Interface:**

```cpp
class Animation {
public:
    virtual ~Animation() {}

    // Called every frame while the event is active.
    // t_rel = time since this event's t_start (animation always sees time from 0).
    virtual void render(hsva_t* buffer, uint8_t length, float t_rel) = 0;
};
```

**Stateless animations** (e.g., wave, spark): output is a pure function of `t_rel` and params. The constructor copies params. No internal buffers.

**Stateful animations** (e.g., shift): need initialization data and a pre-allocated work buffer (see BufferPool below). `render()` computes output from init data + `t_rel`.

**Source layer dependencies:** any animation can declare a dependency on another layer's buffer via the event-level `source_layer` field. The engine passes the source buffer pointer to the animation's constructor. What happens next is the animation's choice:

- **Frozen read:** shift copies the source buffer into its work buffer in the constructor. Later changes to the source layer don't affect it.
- **Live read:** a future animation (e.g., mirror) would store the pointer and re-read the source buffer every frame.

The engine doesn't distinguish — it just passes the pointer. No separate init step or mode flag.

**Key points:**
- Animations see an isolated pixel world: indices 0..N-1. No knowledge of physical layout.
- `t_rel` is computed by the engine: `t_rel = t_program - event.t_start`. The animation always starts from t=0.
- Stateful animations that appear to need mutable frame-to-frame state (like shift) can often be expressed as a pure function of init data + `t_rel`. Example: `pixel_offset = velocity * t_rel` applied to the init data each frame.

### Candidate Animations

| Animation | Description | Stateful? |
|-----------|-------------|-----------|
| **wave** | Sine wave on a single HSV channel. Other channels fixed. | No |
| **spark** | Flash to a color, fade out via alpha decay. | No |
| **paint** | Solid color or per-pixel color array. | No |
| **shift** | Shift pixel pattern left/right at a given velocity. Circular or fill with constant. | Yes (init data) |
| **gradient** | Linear gradient between two colors. | No |
| **sweep** | Band of color moving along the pixels. | No |

### Shift Animation

The shift animation needs initial pixel values to shift. It declares `source=` in the DSL, which sets the event-level `source_layer` field. When the event activates, the engine passes the source layer's buffer to the `AnimShift` constructor, which copies it into its pre-allocated work buffer. This is a frozen read — the shift operates on the snapshot, not the live source.

The source layer must be rendered before the shift reads it. The compiler enforces
`source_layer <= shift's layer index`.

```cpp
struct ShiftParams {
    Direction direction;        // LEFT or RIGHT
    float     velocity;         // pixels per second
    bool      circular;         // wrap around?
    hsva_t    fill_color;       // for non-circular: what fills vacated pixels
    uint8_t   buffer_id;        // index into pre-allocated buffer pool
};
```

Note: `source_layer` is on the `AnimationEvent`, not in `ShiftParams`. This keeps the dependency mechanism generic — any animation type can use it.

---

## AnimationEvent

A scheduled activation of an animation on a layer. Pure data — no runtime behavior.

**Fields:**
- **animation** — which animation type (AnimType enum)
- **t_start** — seconds relative to program start
- **active** — duration in seconds (INFINITY for "runs until program ends")
- **params** — animation-specific parameters (AnimParams union)

```cpp
struct AnimationEvent {
    AnimType   animation;
    float      t_start;
    float      active;
    uint8_t    source_layer;   // 0xFF (SOURCE_NONE) = no dependency
    AnimParams params;
};
```

Note: `layer_id` is not in the event struct — events are grouped per layer in `LayerEvents` (see Program below), so the layer association is structural.

The compiler ensures that no two events on the same layer overlap in time. This is not enforced by the engine — it's a compiler invariant. If violated, later events overwrite earlier ones on the same layer (last write wins). Not an error, just visually wrong.

---

## LayerEvents

The event timeline for a single layer. Events are sorted by `t_start`, with no overlaps (compiler invariant).

```cpp
struct LayerEvents {
    const AnimationEvent* events;    // sorted by t_start
    uint16_t              count;
};
```

Each layer has exactly one `LayerEvents`. This enables per-layer cursor tracking in the engine — each layer's cursor advances independently, avoiding the problem of a global cursor being stuck behind a long-running event while short events on other layers have finished.

---

## Program

The complete animation program loaded onto the ESP32. Contains everything needed for playback.

**Contents:**
- Array of **Layers** (index = priority)
- Array of **LayerEvents** — one per layer, same indexing
- **Buffer pool spec** — sizes of pre-allocated buffers for stateful animations (see Memory)

```cpp
struct Program {
    Layer**       layers;
    LayerEvents*  layer_events;    // one per layer, same indexing
    uint8_t       layer_count;
    // Buffer pool TBD
};
```

One program at a time. Loading a new program replaces the current one entirely (clears all state, re-allocates layers and buffers).

**Current representation:** hardcoded C structs. Binary serialization (for MQTT transport) is a future step — the struct layout is designed to map cleanly to a binary format.

---

## Multi-Strip

Multiple physical strips run on separate ESP32 devices. The compiler partitions events by strip and emits **one blob per strip**. Each blob is completely self-contained — an ESP32 loads its blob, allocates memory, and plays back without any knowledge of other strips.

**Synchronization:** All devices sync their clocks to the base station (NTP or similar) and receive a `START` command with a shared absolute timestamp `T0`. Because all blobs share the same `duration` and all devices start at the same `t_program`, the animations appear synchronized.

**Compiler output:** `build()` returns `dict[str, bytes]` keyed by strip name. The base station routes each blob to the correct device.

**Layer independence:** Layer inference runs per-strip. Events on different strips never share layers, even if they target the same pixel indices or the same time window. This fixes the previous bug where events from two strips with overlapping numeric indices could be silently merged into one layer.

**Cross-strip restrictions:**
- Stateless animations (wave, spark, paint): can be scheduled independently on each strip. Full cross-strip splitting (a single wave spanning N strips as one continuous effect) is a future compiler feature.
- Stateful animations (shift): cross-strip is a compile error. Shift needs a complete pixel snapshot at activation time; this can't be split across independent blobs.

---

## Engine

The runtime that plays a program. Owns the lifecycle of animation instances.

### Per-Layer State

The engine tracks one active animation per layer:

```cpp
struct LayerState {
    uint16_t   cursor;       // index into this layer's events
    Animation* instance;     // currently active animation (null = idle)
};

class Engine {
public:
    Engine(Compositor& compositor);

    void load(Program& program);
    void tick(float t_program);

private:
    Compositor&  _compositor;
    Program*     _program;
    LayerState   _state[MAX_LAYERS];   // one per layer
};
```

The cursor for each layer advances independently. A long-running wave on layer 0 doesn't block the cursor on layer 1 from advancing past short spark events.

### Tick Logic

Each frame, the engine processes each layer independently:

```
for each layer i:
    1. DEACTIVATE: if instance exists and event has ended → destroy instance
    2. ADVANCE CURSOR: skip past fully elapsed events
    3. ACTIVATE/RENDER: if event at cursor is in active window:
       - if no instance → create (constructor does init), render()
       - if instance exists → render()
       (t_rel = t_program - event.t_start)
```

### Factory

A dispatch function creates the right Animation subclass from the event params.
If the event has a `source_layer` (not `SOURCE_NONE`), the factory looks up
the source layer's buffer and passes it to the animation's constructor:

```cpp
Animation* create_animation(const AnimationEvent& e, Program* prog);
```

Future optimization: pre-allocate an animation instance pool instead of new/delete per event (see Memory).

### Active Layer Communication

The engine communicates which layers are active to the compositor via a bitmask (`uint8_t`, supports up to 8 layers):

```cpp
uint8_t active_mask = 0;
for (uint8_t i = 0; i < layer_count; i++) {
    if (_state[i].instance != nullptr)
        active_mask |= (1 << i);
}
_compositor.render(_program->layers, _program->layer_count, active_mask);
```

---

## Compositor

Blends active layers into the Strip, bottom to top.

**Per-frame pipeline:**

1. Clear Strip to black
2. For each layer (index 0 to N-1), if active (bit set in mask):
   - For each pixel `i` in the layer:
     - `physical_idx = layer.index_map[i]`
     - `pixel_rgb = hsv_to_rgb(layer.buffer[i])`
     - `alpha = layer.buffer[i].a`
     - `strip[physical_idx] = lerp(strip[physical_idx], pixel_rgb, alpha)`
3. `FastLED.show()`

```cpp
void Compositor::render(Layer** layers, uint8_t count, uint8_t active_mask)
{
    clear_strip();
    for (uint8_t i = 0; i < count; i++) {
        if (active_mask & (1 << i))
            blend_layer(layers[i]);
    }
}
```

**Blending is always alpha.** Background layers set A=1.0 (full replace). Overlay layers use intermediate alpha for smooth blending. A=0.0 means fully transparent — the layer below shows through.

**Blending happens in RGB space.** HSV blending breaks down when saturation differs. RGB produces visually correct results. Cost is one `hsv_to_rgb` per active pixel per layer per frame — negligible on ESP32.

---

## Memory & BufferPool

Buffers, pool, and temp are allocated once when a program is loaded. Animation instances are `new`/`delete` per event activation — a small allocation per transition, not per frame. Future optimization: placement-new into pre-allocated per-layer storage.

**What needs allocation:**
- Layer index maps and HSVA buffers (from layer definitions)
- Internal buffers for stateful animations (e.g., shift's init data)
- Animation instances themselves

### BufferPool

The base station compiler sees the full timeline. It knows:
- Which animations need internal buffers and when
- Which buffers can be reused (non-overlapping events can share a buffer)

It performs buffer packing (like register allocation) and includes a buffer pool spec in the program: an array of buffer sizes. The ESP32 allocates all buffers at load time.

```cpp
struct BufferPool {
    hsva_t** buffers;       // array of pointers to pre-allocated HSVA arrays
    uint8_t* sizes;         // size (in pixels) of each buffer
    uint8_t  count;         // number of buffers in the pool
};
```

The pool is part of the Program:

```cpp
struct Program {
    Layer**       layers;
    LayerEvents*  layer_events;
    uint8_t       layer_count;
    BufferPool    pool;
};
```

At program load, the engine allocates all buffers:

```cpp
for (uint8_t i = 0; i < program.pool.count; i++)
    program.pool.buffers[i] = new hsva_t[program.pool.sizes[i]];
```

Animation events that need internal storage include a `buffer_id` in their params, pointing to their assigned pool slot. The factory function `create_animation()` looks up the buffer and passes it to the animation's constructor:

```cpp
// On activation — factory handles buffer lookup:
Animation* instance = create_animation(e, _program);
```

The animation stores the buffer pointer — no allocation, no freeing. When two non-overlapping events share a pool slot, one uses it, finishes, then the next one overwrites it.

### Animation Instance Pool

The compiler knows the max number of concurrent animations. The engine can pre-allocate a pool of Animation objects instead of `new`/`delete` per event activation. Details TBD — can be decided at implementation time based on typical concurrency.

---

## Worked Example: Wave + Shift + Alternating Sparks

BPM=120 (1 beat = 500ms). 10 LEDs. 4 beats total (2.0s).

- Beats 1-2: wave on all pixels
- Beats 3-4: shift on all pixels (initialized from wave's last frame)
- Throughout: alternating white/yellow sparks, 2 per beat

### Layers

| Index | Pixels | Role |
|-------|--------|------|
| 0 | [0-9] | background: wave then shift |
| 1 | [0,4] | spark white |
| 2 | [5,9] | spark yellow |

### LayerEvents

**Layer 0:**
```
[0] WAVE   t_start=0.000  active=1.000  params={V channel, ...}
[1] SHIFT  t_start=1.000  active=1.000  source_layer=0  params={...}
```

**Layer 1:**
```
[0] SPARK  t_start=0.000  active=0.100  params={white}
[1] SPARK  t_start=0.500  active=0.100  params={white}
[2] SPARK  t_start=1.000  active=0.100  params={white}
[3] SPARK  t_start=1.500  active=0.100  params={white}
```

**Layer 2:**
```
[0] SPARK  t_start=0.250  active=0.100  params={yellow}
[1] SPARK  t_start=0.750  active=0.100  params={yellow}
[2] SPARK  t_start=1.250  active=0.100  params={yellow}
[3] SPARK  t_start=1.750  active=0.100  params={yellow}
```

### Engine Walkthrough

```
State at start:
  layer[0]: cursor=0, instance=null
  layer[1]: cursor=0, instance=null
  layer[2]: cursor=0, instance=null
```

**t=0.001:**
```
  layer[0]: evt[0] WAVE, 0.0 ≤ 0.001 < 1.0 → CREATE AnimWave, render(t_rel=0.001)
  layer[1]: evt[0] SPARK, 0.0 ≤ 0.001 < 0.1 → CREATE AnimSpark, render(t_rel=0.001)
  layer[2]: evt[0] SPARK, 0.25 > 0.001 → idle

  active_mask = 0b011  (layers 0,1 active)
  compositor blends: layer 0 (wave), layer 1 (spark white)
```

**t=0.105:** (spark white ended)
```
  layer[0]: AnimWave still active → render
  layer[1]: evt[0] ended (0.0+0.1=0.1 < 0.105) → DESTROY, advance cursor to 1
            evt[1] t_start=0.5 > 0.105 → idle
  layer[2]: evt[0] t_start=0.25 > 0.105 → idle

  active_mask = 0b001  (layer 0 only)
```

**t=0.260:** (spark yellow activates)
```
  layer[0]: AnimWave → render
  layer[1]: idle
  layer[2]: evt[0] SPARK, 0.25 ≤ 0.26 < 0.35 → CREATE AnimSpark, render

  active_mask = 0b101  (layers 0,2)
```

**t=1.001:** (wave→shift transition)
```
  layer[0]: evt[0] WAVE ended (0.0+1.0=1.0 < 1.001) → DESTROY AnimWave
            advance cursor to 1
            evt[1] SHIFT, 1.0 ≤ 1.001 < 2.0 → CREATE AnimShift
              source_layer=0 → engine passes layer 0 buffer to constructor
              constructor copies it into work_buf (frozen read)
              render(t_rel=0.001)
  layer[1]: evt[2] SPARK, 1.0 ≤ 1.001 < 1.1 → CREATE AnimSpark, render
  layer[2]: idle (between sparks)

  active_mask = 0b011  (layers 0,1)
```

Sparks continue alternating on layers 1 and 2 through beats 3-4. Shift runs on layer 0 until t=2.0.
