# Elements — Engine Design

> **Status: Implemented.** Matches current code in `src/engine.h`, `src/engine.cpp`.

The engine is the runtime loop. It takes a decoded `Program` and a clock,
and every frame:

1. Advances time
2. Checks which events should be active (start/stop animations)
3. Renders active animations into layer buffers (with remap/scatter)
4. Tells the compositor which layers are active
5. Compositor blends layers → strip → LEDs

---

## Class interface

```cpp
// engine.h
class Engine {
public:
    // Takes ownership of prog (freed on destroy).
    Engine(Program* prog, Strip& strip);
    ~Engine();

    // Advance to time t (seconds since program start).
    // Returns false if program has ended (t >= duration).
    bool tick(float t);

private:
    struct LayerState {
        uint16_t cursor;
        Animation* instance;
    };

    Program* _prog;
    Compositor _compositor;   // owned, constructed with Strip&
    LayerState* _states;      // heap-allocated, one per layer
};
```

Key design decisions:
- **Engine owns the Compositor** (value member, not reference). Constructed from the Strip in Engine's constructor.
- **Engine takes ownership of Program*** — `free_program()` is called in the destructor.
- **LayerState is private** — the cursor and instance are internal to the engine.
- **Layer buffers** are allocated by the engine at construction time (`prog->layers[i].buffer`), not by the decoder.

---

## Cursor design

Each layer has a sorted event list. A single cursor per layer tracks "where are we"
so we don't scan from the beginning every frame.

Layer inference guarantees no overlapping events within a layer, so at most
one event is active at any time. One cursor per layer is sufficient.

Cursor moves forward monotonically. O(1) per layer per frame.

---

## Tick logic

```cpp
bool Engine::tick(float t) {
    if (t >= _prog->duration)
        return false;

    uint32_t active_mask = 0;

    for (uint8_t li = 0; li < _prog->layer_count; li++) {
        LayerDef& layer = _prog->layers[li];
        LayerState& state = _states[li];

        while (state.cursor < layer.event_count) {
            AnimationEvent& e = layer.events[state.cursor];
            float end = e.t_start + e.duration;

            if (t < e.t_start)
                break;  // future event

            if (t < end) {
                // Active — create instance on first frame
                if (!state.instance)
                    state.instance = create_animation(e, _prog, layer);

                float t_rel = t - e.t_start;
                uint8_t len = e.remap_is_identity
                    ? layer.index_map_length : e.remap_length;
                hsva_t* buf = e.remap_is_identity
                    ? layer.buffer : _prog->temp_buffer;

                state.instance->render(buf, len, t_rel);

                if (!e.remap_is_identity)
                    scatter_copy(_prog->temp_buffer, layer.buffer,
                                 e.remap, e.remap_length);
                break;
            }

            // Event finished
            delete state.instance;
            state.instance = nullptr;
            state.cursor++;
        }

        if (state.instance)
            active_mask |= (1u << li);
    }

    _compositor.composite(_prog->layers, _prog->layer_count, active_mask);
    return true;
}
```

---

## Source layer dependencies

Any animation can declare a dependency on another layer's buffer via the
event-level `source_layer` field (`0xFF` = `SOURCE_NONE`). The engine looks up the
source layer's buffer and passes it to the animation's constructor. What the
animation does with that buffer is its own business:

- **Frozen read (copy once):** `AnimShift` — the engine pre-fills the shift's
  work buffer with source pixels in the dependent's pixel order. The shift
  operates on this snapshot.
- **Live read (pointer):** a future animation (e.g., mirror) could store the
  pointer and re-read the source buffer every frame.

**Ordering invariant:** `source_layer < dependent_layer`. The engine renders
layers in order 0, 1, 2, ... so the source layer is always rendered before the
dependent. The compiler enforces this.

**Buffer clobber edge case:** if the source event has ended and another event on
the source layer is active, the source buffer may contain unexpected data. The
compiler emits a **warning** (not an error) for this case.

---

## Animation class hierarchy

Animations are subclasses of `Animation` with virtual `render()`. Initialization
happens in the constructor — no separate `init()` method.

```cpp
// animation.h
class Animation {
public:
    virtual ~Animation() {}
    virtual void render(hsva_t* buffer, uint8_t length, float t) = 0;
};
```

Concrete subclasses:

| Class | Params | State |
|-------|--------|-------|
| `AnimWave` | `WaveParams` | Stateless |
| `AnimSpark` | `SparkParams` | Stateless |
| `AnimPaint` | `PaintParams` | Stateless |
| `AnimShift` | `ShiftParams` + work buffer | Stateful (work buffer pre-filled by engine) |

---

## Factory function

The engine calls this when an event first becomes active:

```cpp
static Animation* create_animation(const AnimationEvent& e, Program* prog,
                                   const LayerDef& dep_layer)
{
    switch (e.params.type) {
        case ANIM_WAVE:  return new AnimWave(e.params.wave);
        case ANIM_SPARK: return new AnimSpark(e.params.spark);
        case ANIM_PAINT: return new AnimPaint(e.params.paint);

        case ANIM_SHIFT: {
            hsva_t* work = prog->pool.buffers[e.params.shift.buffer_id];
            uint8_t shift_len = e.remap_is_identity
                ? dep_layer.index_map_length : e.remap_length;

            if (e.source_layer != SOURCE_NONE) {
                const LayerDef& src_layer = prog->layers[e.source_layer];
                // Build physical→source_logical lookup, copy source
                // pixels in dependent's pixel order into work buffer
                // (see engine.cpp for full implementation)
                ...
            } else {
                memset(work, 0, shift_len * sizeof(hsva_t));
            }

            return new AnimShift(e.params.shift, work, shift_len);
        }
        default: return nullptr;
    }
}
```

Note: `AnimShift` takes 3 arguments — `(ShiftParams, work_buf, work_len)`.
The engine pre-fills the work buffer with source pixels before constructing
the shift. The shift doesn't know about source layers — it just operates
on whatever is in its work buffer.

---

## Remap and scatter

Animations always render into a contiguous buffer of length `remap_length`.
When `remap_is_identity` is true, the animation writes directly into the
layer buffer. When false, it writes into `prog->temp_buffer`, and the engine
scatter-copies back:

```cpp
static inline void scatter_copy(const hsva_t* src, hsva_t* dst,
                                const uint8_t* remap, uint8_t len)
{
    for (uint8_t i = 0; i < len; i++)
        dst[remap[i]] = src[i];
}
```

---

## Layer buffer clearing

When no event is active on a layer, the compositor skips it entirely (active
mask bit is 0). No need to zero the buffer — compositor ignores it.

When an event ends, the buffer isn't explicitly cleared. Next active event
will overwrite it. If no event follows, the layer is inactive.

---

## Clock source

Engine takes `float t` (seconds) as a parameter — doesn't own the clock.
The PlaybackDevice computes `t_rel` from its clock subsystem and passes it in.

---

## Program end policy

When `t >= program.duration`, `tick()` returns `false`. The engine stops
rendering. Loop support is future work, controlled at the PlaybackDevice level.
