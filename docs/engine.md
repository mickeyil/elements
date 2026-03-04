# Elements v2 — Engine Design

The engine is the runtime loop. It takes a decoded `Program` and a clock,
and every frame:

1. Advances time
2. Checks which events should be active (start/stop animations)
3. Renders active animations into layer buffers (with remap/scatter)
4. Tells the compositor which layers are active
5. Compositor blends layers → strip → LEDs

## What exists today

- `main.cpp` manually creates animations and wires them to layers — no
  scheduling, no lifecycle
- `Layer`, `Compositor`, `Strip` — the rendering stack works
- `AnimWave`, `AnimSpark` — as classes with virtual `render()`, will become
  free functions

## What the engine needs

```
Engine::tick(float t)
    for each layer:
        walk event list with cursor
        activate events whose t_start <= t
        deactivate events whose t_start + duration <= t
        for each active event:
            compute t_rel = t - event.t_start
            render into layer buffer (direct or scatter)
        update active_mask bit
    compositor.composite(active_mask)
    strip.show()
```

---

## Open Questions

### 1. Cursor design

Each layer has a sorted event list. We need a cursor (index) to track
"where are we" so we don't scan from the beginning every frame.

**Decision: single cursor per layer.**

Layer inference guarantees no overlapping events within a layer, so at most
one event is active at any time. One cursor per layer is sufficient.

```cpp
struct LayerState {
    uint16_t cursor;    // index into events array
    bool has_active;    // is an event currently rendering?
};

void Engine::tick(float t) {
    uint32_t active_mask = 0;

    for (uint8_t li = 0; li < prog->layer_count; li++) {
        LayerDef& layer = prog->layers[li];
        LayerState& state = layer_states[li];

        while (state.cursor < layer.event_count) {
            AnimationEvent& e = layer.events[state.cursor];
            float end = e.t_start + e.duration;

            if (t < e.t_start) {
                // Haven't reached this event yet
                state.has_active = false;
                break;
            }
            if (t < end) {
                // This event is active
                state.has_active = true;
                float t_rel = t - e.t_start;
                render_event(e, layer, t_rel);
                break;
            }
            // Event finished — advance cursor
            state.cursor++;
            state.has_active = false;
        }

        if (state.has_active) active_mask |= (1u << li);
    }

    compositor.composite(active_mask);
}
```

Cursor moves forward monotonically. O(1) per layer per frame.

Add a debug assertion at load time to verify the compiler's guarantee:

```cpp
#ifndef NDEBUG
for (uint16_t i = 1; i < layer.event_count; i++) {
    assert(layer.events[i].t_start >=
           layer.events[i-1].t_start + layer.events[i-1].duration);
}
#endif
```

---

### 2. Shift init/snapshot

When a shift event activates, it needs to snapshot another layer's buffer.
At what exact moment? On the first frame where `t >= t_start`.

**Constraint:** the source layer must be fully rendered before the shift reads
it. This implies a **layer rendering order dependency** — source layer must
render before the shift's layer.

The compiler stores `source_layer` in the shift params. The engine should
render layers in order 0, 1, 2, ... which naturally satisfies this if the
compiler places source layers before dependent layers.

_(TBD: verify compiler assigns layer indices such that source_layer < shift's layer index)_

---

### 3. Render function signatures

Current `AnimWave::render(hsva_t* buf, uint8_t len, float t)` takes absolute
time. Engine should pass `t_rel` (time since event start).

Shift also needs access to the buffer pool (for its work buffer) and the
source layer's buffer (for snapshot).

Proposed unified approach — free functions, not virtual methods:

```cpp
void render_wave(const WaveParams& p, hsva_t* buf, uint8_t len, float t_rel);

void render_spark(const SparkParams& p, hsva_t* buf, uint8_t len, float t_rel);

void render_shift(const ShiftParams& p, hsva_t* buf, uint8_t len,
                  float t_rel, hsva_t* work_buf,
                  const hsva_t* source_buf, uint8_t source_len,
                  bool first_frame);
```

Dispatch in engine:

```cpp
void render_event(const AnimationEvent& e, LayerDef& layer,
                  float t_rel, Program* prog) {
    uint8_t len = e.remap_length;
    hsva_t* buf = e.remap_is_identity
        ? layer.buffer
        : prog->temp_buffer;

    switch (e.params.type) {
        case ANIM_WAVE:
            render_wave(e.params.wave, buf, len, t_rel);
            break;
        case ANIM_SPARK:
            render_spark(e.params.spark, buf, len, t_rel);
            break;
        case ANIM_SHIFT: {
            hsva_t* work = prog->pool.buffers[e.params.shift.buffer_id];
            uint8_t sl = e.params.shift.source_layer;
            render_shift(e.params.shift, buf, len, t_rel,
                         work, prog->layers[sl].buffer,
                         prog->layers[sl].index_map_length,
                         /* first_frame= */ t_rel == 0.0f);
            break;
        }
    }

    if (!e.remap_is_identity) {
        scatter_copy(prog->temp_buffer, layer.buffer, e.remap, len);
    }
}
```

---

### 4. Layer buffer clearing

When no event is active on a layer, the compositor skips it entirely (active
mask bit is 0). No need to zero the buffer — compositor ignores it.

When an event ends, the buffer isn't explicitly cleared. Next active event
will overwrite it. If no event follows, the layer is inactive.

---

### 5. Multiple active events per layer

Layer inference prevents this. At most one event active per layer at any time.
Enforced by the debug assertion in #1.

---

### 6. Clock source

Engine takes `float t` (seconds) as a parameter — doesn't own the clock.

```cpp
// ESP32 (Arduino)
engine.tick(millis() / 1000.0f);

// Desktop / SDL
engine.tick(SDL_GetTicks() / 1000.0f);
```

---

### 7. Program end policy

When `t > program.duration`:
- **Stop** — engine stops calling render, leaves last frame on strip
- **Loop** — engine wraps t: `t_rel = fmod(t, program.duration)`, resets cursors

_(TBD: decide based on use case. Default: stop.)_

---

## Layer buffer ownership

Each `LayerDef` needs an `hsva_t* buffer` field — not currently in the
decoded struct. Size = `index_map_length`. Allocated by the engine at load
time (not the decoder — the decoder doesn't know about render buffers).

The engine owns layer buffers. The decoder owns params/events/remaps.

---

## Struct additions needed

```cpp
// In decoder.h — add to LayerDef:
hsva_t* buffer;  // allocated by Engine, not decoder

// New in engine.h:
struct LayerState {
    uint16_t cursor;
    bool has_active;
};

class Engine {
    Program* prog;
    LayerState* layer_states;  // [layer_count]
    // Layer buffers are in prog->layers[i].buffer
public:
    Engine(Program* prog);
    ~Engine();
    void tick(float t);
};
```
