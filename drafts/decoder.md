# Elements v2 — C++ Decoder Design

The decoder reads a binary blob (produced by the Python compiler) and builds
a `Program` struct in memory. Runs on ESP32. One program loaded at a time.

## Memory Strategy: Arena Allocation

Single `malloc` for the entire program. Compute total size from the blob
header, allocate once, lay out all data contiguously. Zero fragmentation.
Load a new program → free the old arena → allocate a new one.

```
┌──────────────────────────────────────────────────┐
│ Arena (one malloc)                               │
│                                                  │
│  Program header                                  │
│  BufferPool: hsva_t* pool[buffer_count]          │
│  Buffer data: hsva_t[size0], hsva_t[size1], ...  │
│  Layer[0]: index_map, events                     │
│  Layer[1]: index_map, events                     │
│  ...                                             │
│  AnimationEvent[0]: type, timing, remap, params  │
│  AnimationEvent[1]: ...                          │
│  ...                                             │
│  Remap arrays (variable length, packed)          │
│  Temp render buffer: hsva_t[max_remap_length]    │
│                                                  │
└──────────────────────────────────────────────────┘
```

### Size calculation

From blob header alone we know:
- `layer_count`, `buffer_count`, `max_remap_length`, `duration`

From parsing the blob we accumulate:
- Total events across all layers
- Total index map bytes
- Total remap bytes
- Buffer pool sizes

Total arena size:
```
sizeof(Program)
+ layer_count * sizeof(Layer)
+ total_events * sizeof(AnimationEvent)
+ total_index_map_bytes
+ total_remap_bytes
+ sum(buffer_sizes) * sizeof(hsva_t)     // work buffers
+ max_remap_length * sizeof(hsva_t)      // temp render buffer
```

### Two-pass decode

**Pass 1:** scan the blob to compute sizes (don't allocate yet).
**Pass 2:** allocate arena, walk blob again, populate structs with pointers
into the arena.

This avoids over-allocation and keeps the decoder simple.

---

## Target Structs

```cpp
// Matches blob animation type IDs
enum AnimType : uint8_t {
    ANIM_WAVE  = 0,
    ANIM_SHIFT = 1,
    ANIM_SPARK = 2,
    ANIM_FILL  = 3,
};

// Animation params — tagged union, no vtables
struct AnimParams {
    AnimType type;
    union {
        struct {
            uint8_t channel;  // 0=H, 1=S, 2=V
            float h, s;
            float min_val, max_val;
            float period, phase0, pixel_step;
        } wave;

        struct {
            uint8_t direction;  // 0=left, 1=right
            float velocity;
            uint8_t circular;
            float fill_h, fill_s, fill_v, fill_a;
            uint8_t init_mode;    // 0=CONST, 1=SNAPSHOT
            uint8_t source_layer;
            uint8_t buffer_id;
        } shift;

        struct {
            float color_h, color_s, color_v;
            float fade;
        } spark;

        struct {
            float color_h, color_s, color_v;
        } fill;
    };
};

struct AnimationEvent {
    AnimParams params;
    float t_start;         // seconds
    float duration;        // seconds
    uint8_t remap_length;
    bool remap_is_identity;
    uint8_t* remap;        // → points into arena
};

struct LayerDef {
    uint8_t index_map_length;
    uint8_t* index_map;     // → points into arena
    uint16_t event_count;
    AnimationEvent* events; // → points into arena
};

struct Program {
    float duration;          // seconds
    uint8_t layer_count;
    uint8_t buffer_count;
    uint8_t max_remap_length;

    LayerDef* layers;        // → points into arena
    hsva_t** buffers;        // → array of pointers into arena
    hsva_t* temp_buffer;     // → points into arena

    void* _arena;            // raw allocation, for freeing
};
```

---

## Animation Instantiation

The blob stores animation *definitions* (type enum + params). Two approaches:

### Option A: Tagged union (chosen)

No virtual dispatch. `AnimParams` is a tagged union — the engine switches on
`params.type` at render time:

```cpp
void render_event(const AnimationEvent& event, hsva_t* buffer,
                  uint8_t length, float t_rel)
{
    switch (event.params.type) {
        case ANIM_WAVE:
            render_wave(event.params.wave, buffer, length, t_rel);
            break;
        case ANIM_SPARK:
            render_spark(event.params.spark, buffer, length, t_rel);
            break;
        case ANIM_SHIFT:
            render_shift(event.params.shift, buffer, length, t_rel);
            break;
        // ...
    }
}
```

**Why not polymorphic classes (AnimWave, AnimShift, etc.)?**
- Arena allocation with vtables requires placement new + manual destructor
  calls — fragile
- Virtual dispatch overhead on every frame for every active animation
- Tagged union is simpler, smaller, cache-friendlier
- Adding a new animation type = add a struct to the union + a case to the
  switch — same effort as adding a subclass

**Impact on existing code:** the current `Animation` base class with virtual
`render()` would be replaced by free functions (`render_wave`, `render_spark`,
etc.) that take the params struct directly. The math stays identical — just
reorganized.

### Option B: Polymorphic (not chosen)

Keep `Animation*` base class, use placement new into the arena:
```cpp
AnimWave* w = new (arena_ptr) AnimWave(params...);
```
More familiar OOP pattern but adds complexity for arena lifecycle management.

---

## Decode Flow

```cpp
Program* decode_program(const uint8_t* blob, size_t len)
{
    // Pass 1: scan blob, compute arena size
    size_t arena_size = compute_arena_size(blob, len);

    // Allocate arena
    void* arena = malloc(arena_size);
    if (!arena) return nullptr;

    // Pass 2: populate structs
    // Walk blob sequentially, copy data into arena,
    // set up pointers between structs
    Program* prog = (Program*)arena;
    uint8_t* cursor = (uint8_t*)arena + sizeof(Program);

    // ... layers, events, index maps, remaps, buffers
    // each carved out of cursor, cursor advances

    prog->_arena = arena;
    return prog;
}

void free_program(Program* prog)
{
    if (prog) free(prog->_arena);
}
```

---

## Remap Storage

Variable-length remap arrays are packed contiguously in the arena.
Each `AnimationEvent.remap` points into this region.

```
arena:
  ... events ...
  [remap0: 0,1,2,...,9] [remap1: 0,1,2,...,9] [remap2: 0,1] [remap3: 2,3] ...
```

The `remap_is_identity` flag tells the engine to skip scatter copy.
When identity, the remap pointer is still valid but the engine won't read it.

---

## Endianness

ESP32 is little-endian. Blob is little-endian. No byte swapping needed.
`memcpy` directly into float/uint fields.

Desktop (x86/x64) is also little-endian — tests work without conversion.

Optional: add a compile-time static assert:
```cpp
static_assert(__BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__,
              "blob format assumes little-endian");
```

---

## Validation (at decode time)

Minimal — the Python compiler already validates. But add safety checks:
- Magic == "ELEM"
- Version == 1
- Blob length sufficient for declared contents
- layer_count <= 32
- No index_map entry >= 255 (uint8 strip limit)

Return `nullptr` on any failure.

---

## Testing (Catch2, desktop)

Test the decoder on desktop by:
1. Using the Python compiler to produce a known blob (test animation)
2. Loading it in C++ test, calling `decode_program()`
3. Verifying struct contents match expected values

Tests:
- Header fields (duration, layer_count, buffer_count)
- Layer 0 index map == [0..9], 2 events (wave + shift)
- Layer 1 index map == [0,4,5,9], 8 events (all sparks)
- Wave params (channel, h, s, period, etc.)
- Shift params (direction, velocity, init_mode=SNAPSHOT, source_layer=0)
- Spark remaps ([0,1] for white, [2,3] for yellow)
- remap_is_identity flags
- Buffer pool == [10]
- Invalid blob → nullptr

### Test blob generation

Save the test blob from Python:
```python
from elements.dsl import *

def program(beat, duration):
    # ... test animation ...
    return build(beat=beat, duration=duration)

blob = program(beat=0.5, duration=2.0)
open("test_animation.bin", "wb").write(blob)
```

Include `test_animation.bin` as a test fixture (embed as C array or load from file).

---

## Open Questions

_(none — ready to implement)_
