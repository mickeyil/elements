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
- `AnimWave`, `AnimSpark` — as classes with virtual `render()`

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
    uint16_t   cursor;    // index into events array
    Animation* instance;  // currently active (null = idle)
};

void Engine::tick(float t) {
    uint32_t active_mask = 0;

    for (uint8_t li = 0; li < prog->layer_count; li++) {
        LayerDef& layer = prog->layers[li];
        LayerState& state = layer_states[li];

        while (state.cursor < layer.event_count) {
            AnimationEvent& e = layer.events[state.cursor];
            float end = e.t_start + e.duration;

            if (t < e.t_start) break;

            if (t < end) {
                // Event is active — create instance on first frame
                if (!state.instance)
                    state.instance = create_animation(e, prog);

                float t_rel = t - e.t_start;
                uint8_t len = e.remap_length;
                hsva_t* buf = e.remap_is_identity
                    ? layer.buffer : prog->temp_buffer;

                state.instance->render(buf, len, t_rel);

                if (!e.remap_is_identity)
                    scatter_copy(prog->temp_buffer, layer.buffer,
                                 e.remap, len);
                break;
            }

            // Event finished — destroy instance, advance cursor
            delete state.instance;
            state.instance = nullptr;
            state.cursor++;
        }

        if (state.instance) active_mask |= (1u << li);
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
This happens in the `AnimShift` constructor — the engine creates the instance
on the first frame where `t >= t_start`, and the constructor copies the
source layer's buffer into the pre-allocated work buffer. No separate init
step, no `first_frame` flag.

**Constraint:** the source layer must be fully rendered before the shift reads
it. This implies a **layer rendering order dependency** — source layer must
render before the shift's layer.

The compiler stores `source_layer` in the shift params. The engine renders
layers in order 0, 1, 2, ... which naturally satisfies this if the compiler
places source layers before dependent layers.

_(TBD: verify compiler assigns layer indices such that source_layer < shift's layer index)_

---

### 3. Animation class hierarchy

Animations are subclasses of `Animation` with virtual `render()`. The engine
passes `t_rel` (time since event start). Initialization happens in the
constructor — no separate `init()` method.

```cpp
class Animation {
public:
    virtual ~Animation() {}
    virtual void render(hsva_t* buffer, uint8_t length, float t_rel) = 0;
};

class AnimWave : public Animation {
    WaveParams p;
public:
    AnimWave(const WaveParams& params) : p(params) {}
    void render(hsva_t* buffer, uint8_t length, float t_rel) override;
};

class AnimSpark : public Animation {
    SparkParams p;
public:
    AnimSpark(const SparkParams& params) : p(params) {}
    void render(hsva_t* buffer, uint8_t length, float t_rel) override;
};

class AnimShift : public Animation {
    ShiftParams p;
    hsva_t* work_buf;
public:
    // Constructor snapshots source_buf into work_buf
    AnimShift(const ShiftParams& params, hsva_t* work_buf,
              const hsva_t* source_buf, uint8_t source_len);
    void render(hsva_t* buffer, uint8_t length, float t_rel) override;
};
```

Factory function — the engine calls this when an event first becomes active:

```cpp
Animation* create_animation(const AnimationEvent& e, Program* prog) {
    switch (e.params.type) {
        case ANIM_WAVE:
            return new AnimWave(e.params.wave);
        case ANIM_SPARK:
            return new AnimSpark(e.params.spark);
        case ANIM_SHIFT: {
            uint8_t sl = e.params.shift.source_layer;
            return new AnimShift(
                e.params.shift,
                prog->pool.buffers[e.params.shift.buffer_id],
                prog->layers[sl].buffer,
                prog->layers[sl].index_map_length);
        }
        default:
            return nullptr;
    }
}
```

Remap/scatter logic is in the engine tick loop (see Section 1), not inside
the animation. Animations always render into a contiguous buffer of length
`remap_length`. The engine handles the scatter copy when `remap_is_identity`
is false.

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
    uint16_t   cursor;    // index into events array
    Animation* instance;  // currently active (null = idle)
};

class Engine {
    Program* prog;
    LayerState* layer_states;  // [layer_count]
    Compositor& compositor;
    // Layer buffers are in prog->layers[i].buffer
public:
    Engine(Program* prog, Compositor& compositor);
    ~Engine();
    void tick(float t);
};
```
