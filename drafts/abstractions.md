# Elements v2 — Abstractions

This document defines the core abstractions for the v2 animation engine. It is the result of iterative design discussion and supersedes the "Rendering Abstractions" and "Animation System" sections in [v2_design.md](v2_design.md) where they conflict.

---

## Overview

The ESP32 is a dumb playback engine. All heavy lifting — analysis, scheduling, memory planning — happens on the base station, which compiles a **Program** and sends it to the device. The device loads the program, allocates memory once, and plays it back with zero runtime allocation.

The rendering pipeline:

```
Program (static data)
  → Engine (scans timeline, manages animation lifecycle)
    → Animations (render into layer buffers)
      → Compositor (blends layers into strip, bottom to top)
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
- **Layers are static.** All layers are defined at program load and live for the entire program. No creation or destruction during playback. The compiler determines the required layers; the engine allocates them once.
- **Layers may overlap in physical indices.** A background layer on [0..49] and a spark layer on [4,11,21,25] both address physical LEDs 4, 11, 21, and 25. The compositor resolves this via priority ordering and alpha blending.
- **Per-pixel alpha.** Alpha is in the HSVA buffer per pixel, not per layer. This enables effects like cascading sparks where each pixel fades independently.

**Analogy:** Layers are like tracks in a DAW. Bottom layers are long-running backgrounds. Upper layers are sparse, short-lived effects aligned to beats or other events. The compiler packs animation events into layers (tracks) such that no two events on the same layer overlap in time.

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

    // Called once when the event activates.
    // Can allocate internal state, copy params, snapshot other layers.
    virtual void init(const AnimParams& params, uint8_t length,
                      Layer** layers, uint8_t layer_count) = 0;

    // Called every frame while the event is active.
    // t_rel = time since this event's t_start (animation always sees time from 0).
    virtual void render(hsva_t* buffer, uint8_t length, float t_rel) = 0;
};
```

**Stateless animations** (e.g., wave, spark): output is a pure function of `t_rel` and params. `init()` just copies params. No internal buffers.

**Stateful animations** (e.g., shift): need initialization data — either constant values embedded in params, or a runtime snapshot of another layer's buffer. `init()` allocates internal state (from the pre-allocated buffer pool — see Memory below). `render()` computes output from init data + `t_rel`.

**Key points:**
- Animations see an isolated pixel world: indices 0..N-1. No knowledge of physical layout.
- `t_rel` is computed by the engine: `t_rel = t_program - event.t_start`. The animation always starts from t=0.
- Stateful animations that appear to need mutable frame-to-frame state (like shift) can often be expressed as a pure function of init data + `t_rel`. Example: `pixel_offset = velocity * t_rel` applied to the init data each frame.

### Candidate Animations

| Animation | Description | Stateful? |
|-----------|-------------|-----------|
| **wave** | Sine wave on a single HSV channel. Other channels fixed. | No |
| **spark** | Flash to a color, fade out via alpha decay. | No |
| **fill** | Solid color fill. | No |
| **shift** | Shift pixel pattern left/right at a given velocity. Circular or fill with constant. | Yes (init data) |
| **gradient** | Linear gradient between two colors. | No |
| **sweep** | Band of color moving along the pixels. | No |

### Shift Animation — Init Modes

The shift animation needs initial pixel values to shift. Two modes:

1. **CONST** — pixel values embedded in the animation event params. Predetermined by the compiler.
2. **SNAPSHOT** — copies another layer's buffer at the moment the event activates. The source layer (referenced by index) must have an active event at that time — the compiler ensures this.

```cpp
struct ShiftParams {
    Direction direction;        // LEFT or RIGHT
    float     velocity;         // pixels per second
    bool      circular;         // wrap around?
    hsva_t    fill_color;       // for non-circular: what fills vacated pixels
    InitMode  init_mode;        // CONST or SNAPSHOT
    // CONST: init_pixels[] in pre-allocated buffer
    // SNAPSHOT: source_layer index + offset
    uint8_t   source_layer;
    uint8_t   source_offset;
    uint8_t   buffer_id;        // index into pre-allocated buffer pool
};
```

---

## AnimationEvent

A scheduled activation of an animation on a layer. Pure data — no runtime behavior.

**Fields:**
- **layer_id** — index into the program's layer array (= which layer to render into)
- **animation** — which animation type (AnimType enum)
- **t_start** — seconds relative to program start
- **active** — duration in seconds (INFINITY for "runs until program ends")
- **params** — animation-specific parameters (AnimParams union)

```cpp
struct AnimationEvent {
    uint8_t    layer_id;
    AnimType   animation;
    float      t_start;
    float      active;
    AnimParams params;
};
```

Events are sorted by `t_start` in the program. The engine scans them with a cursor.

The compiler ensures that no two events on the same layer overlap in time. This is not enforced by the engine — it's a compiler invariant. If violated, later events overwrite earlier ones on the same layer (last write wins). Not an error, just visually wrong.

---

## Program

The complete animation program loaded onto the ESP32. Contains everything needed for playback.

**Contents:**
- Array of **Layers** (index = priority)
- Array of **AnimationEvents** (sorted by `t_start`)
- **Buffer pool spec** — sizes of pre-allocated buffers for stateful animations (see Memory)

```cpp
struct Program {
    Layer**                layers;
    uint8_t                layer_count;
    const AnimationEvent*  events;
    uint16_t               event_count;
    // Buffer pool TBD
};
```

One program at a time. Loading a new program replaces the current one entirely (clears all state, re-allocates layers and buffers).

**Current representation:** hardcoded C structs. Binary serialization (for MQTT transport) is a future step — the struct layout is designed to map cleanly to a binary format.

---

## Engine

The runtime that plays a program. Owns the lifecycle of animation instances.

**Responsibilities per frame (`tick(t_program)`):**

1. Scan the event timeline using a cursor
2. For events that just became active (crossed `t_start`): create Animation instance, call `init()`
3. For events that just ended (crossed `t_start + active`): destroy Animation instance
4. For all currently active events: call `render(buffer, length, t_rel)` where `t_rel = t_program - event.t_start`
5. Hand layers to the compositor

**Cursor optimization:** Events are sorted by `t_start`. The cursor advances past events whose `t_start + active` is in the past — they'll never activate again. The scan only looks at events from the cursor forward, and stops when it hits an event whose `t_start` is in the future.

**Animation instance tracking:** The engine maps active events to their Animation instances. Details TBD — likely a fixed-size array indexed by event index or layer index.

**Factory:** A dispatch function creates the right Animation subclass from the AnimType enum.

```cpp
class Engine {
public:
    Engine(Compositor& compositor);

    void load(Program& program);     // set up layers, reset state
    void tick(float t_program);      // per-frame update

private:
    Compositor&    _compositor;
    const Program* _program;
    uint16_t       _cursor;
    // Active animation instance tracking — TBD
};
```

---

## Compositor

Blends all active layers into the Strip, bottom to top.

**Per-frame pipeline:**

1. Clear Strip to black
2. For each layer (index 0 to N-1), if the layer has an active animation this frame:
   - For each pixel `i` in the layer:
     - `physical_idx = layer.index_map[i]`
     - `pixel_rgb = hsv_to_rgb(layer.buffer[i])`
     - `alpha = layer.buffer[i].a`
     - `strip[physical_idx] = lerp(strip[physical_idx], pixel_rgb, alpha)`
3. `FastLED.show()`

**Blending is always alpha.** Background layers set A=1.0 (full replace). Overlay layers use intermediate alpha for smooth blending. A=0.0 means fully transparent — the layer below shows through.

**Blending happens in RGB space.** HSV blending breaks down when saturation differs. RGB produces visually correct results. Cost is one `hsv_to_rgb` per active pixel per layer per frame — negligible on ESP32.

**"Active layer" detection:** The compositor needs to know which layers have active events this frame. The engine tells it (either via a bitmask, or by only passing active layers). Layers with no active event are skipped entirely — their stale buffer contents are ignored.

---

## Memory

No `malloc`/`free` during playback. All memory is allocated once when a program is loaded.

**What needs allocation:**
- Layer index maps and HSVA buffers (from layer definitions)
- Internal buffers for stateful animations (e.g., shift's init data)
- Animation instances themselves

**Compiler's role:** The base station compiler sees the full timeline. It knows:
- How many layers and their sizes
- Which animations need internal buffers and when
- Which buffers can be reused (non-overlapping events can share a buffer)

It performs buffer packing (like register allocation) and includes a **buffer pool spec** in the program: an array of buffer sizes. The ESP32 allocates all buffers at load time. Animation events reference buffers by pool index.

```
Buffer pool: [
    { size: 10 },   // buffer 0: 10 HSVA pixels
    { size: 4 },    // buffer 1: 4 HSVA pixels
]
```

Animation events that need internal storage include a `buffer_id` in their params, pointing to their assigned pool slot.

**Animation instance pool:** Similarly, the compiler knows the max number of concurrent animations. The engine can pre-allocate a pool of Animation objects. Details TBD.

---

## Worked Example: Current Demo as a Program

The existing hardcoded wave + spark demo expressed as a program:

```cpp
static const uint8_t bg_indices[] = {0, 1};
static const uint8_t spark_indices[] = {0, 1};

Layer bg_layer(bg_indices, 2);
Layer spark_layer(spark_indices, 2);

Layer* demo_layers[] = { &bg_layer, &spark_layer };

static const AnimationEvent demo_events[] = {
    // Layer 0: slow brightness wave, runs forever
    {0, AnimType::WAVE, 0.0f, INFINITY,
        {.wave = {WaveChannel::V, 220.0f, 1.0f, 0.0f,
                  0.0f, 0.4f, 8.0f, -M_PI/2, M_PI}}},
    // Layer 1: periodic spark, runs forever
    {1, AnimType::SPARK, 0.0f, INFINITY,
        {.spark = {0.25f}}},
};

Program demo_program = {
    demo_layers, 2,
    demo_events, 2
};
```

```cpp
Engine engine(compositor);

void setup() {
    FastLED.addLeds<WS2811, LED_PIN, GRB>(crgb, NUM_LEDS);
    FastLED.setBrightness(255);
    engine.load(demo_program);
}

void loop() {
    float t = millis() / 1000.0f;
    engine.tick(t);
    FastLED.show();
    delay(FRAME_PERIOD_MS);
}
```

---

## Worked Example: Music Sync — Sparks on Beats

A 128 BPM track. Background wave on all 50 LEDs. Sparks on select pixels every 4th beat (every 1.875s). Each spark fades over one beat (468ms).

```
Program:
  Layers:
    [0] indices=[0..49]                    // background, lowest priority
    [1] indices=[4, 11, 21, 25]            // spark group A
    [2] indices=[7, 19, 33, 42, 48]        // spark group B

  Events (sorted by t_start):
    WAVE  on layer 0, t_start=0.000, active=INF, params={hue wave}

    SPARK on layer 1, t_start=12.500, active=0.468, params={fade=0.468}
    SPARK on layer 2, t_start=14.375, active=0.468, params={fade=0.468}
    SPARK on layer 1, t_start=16.250, active=0.468, params={fade=0.468}
    SPARK on layer 2, t_start=18.125, active=0.468, params={fade=0.468}
    ...
```

Each spark event is a separate entry. The compiler generated them from the beat analysis. Layers 1 and 2 alternate — same concept as DAW tracks, the compiler packed non-overlapping events onto the minimum number of layers.
