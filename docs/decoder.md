# Elements — C++ Decoder Design

> **Status: Implemented.** Matches current code in `src/decoder.h`, `src/decoder.cpp`.

The decoder reads a binary blob (produced by the Python compiler) and builds
a `Program` struct in memory. Runs on ESP32. One program loaded at a time.

## Memory Strategy: Per-object allocation

The decoder allocates each component individually (`new[]` for arrays, `new` for
the Program itself). `free_program()` walks the structure and frees everything
recursively. This is simpler than arena allocation and sufficient for the
"allocate once at load, free once at teardown" usage pattern.

```
Program (new)
├── layers (new LayerDef[])
│   ├── [0].index_map (new uint8_t[])
│   ├── [0].events (new AnimationEvent[])
│   │   ├── [0].remap (new uint8_t[])
│   │   ├── [1].remap (new uint8_t[])
│   │   └── ...
│   ├── [0].buffer ← allocated by Engine, not decoder
│   ├── [1].index_map ...
│   └── ...
├── pool.sizes (new uint8_t[])
├── pool.buffers (new hsva_t*[])
│   ├── [0] (new hsva_t[])
│   └── ...
└── temp_buffer (new hsva_t[])
```

`free_program()` walks this tree in reverse, deleting each allocation.
The Engine destructor calls `free_program()`.

---

## Target Structs

Defined in `decoder.h`:

```cpp
enum AnimType : uint8_t {
    ANIM_WAVE  = 0,
    ANIM_SHIFT = 1,
    ANIM_SPARK = 2,
    ANIM_PAINT = 3,
};

struct WaveParams {
    uint8_t channel;  // 0=H, 1=S, 2=V
    float h, s, v;
    float min_val, max_val;
    float period, phase0, pixel_step;
};

struct ShiftParams {
    uint8_t direction;  // 0=left, 1=right
    float velocity;
    uint8_t circular;
    float fill_h, fill_s, fill_v, fill_a;
    uint8_t buffer_id;
};

struct SparkParams {
    float color_h, color_s, color_v;
    float fade;
};

struct PaintParams {
    uint8_t mode;         // 0=solid, 1=per_pixel
    float color_h, color_s, color_v, color_a;  // solid mode
    uint8_t pixel_count;  // per_pixel mode
    hsva_t* pixels;       // heap-allocated (per_pixel mode), nullptr for solid
};

struct AnimParams {
    AnimType type;
    union {
        WaveParams  wave;
        ShiftParams shift;
        SparkParams spark;
        PaintParams paint;
    };
};

struct AnimationEvent {
    AnimParams params;
    float t_start;          // seconds
    float duration;         // seconds
    uint8_t source_layer;   // 0xFF = SOURCE_NONE
    uint8_t remap_length;
    bool remap_is_identity;
    uint8_t* remap;         // heap-allocated array
};

struct LayerDef {
    uint8_t index_map_length;
    uint8_t* index_map;      // heap-allocated array
    uint16_t event_count;
    AnimationEvent* events;  // heap-allocated array
    hsva_t* buffer;          // engine-allocated, size = index_map_length
};

struct BufferPool {
    uint8_t count;
    uint8_t* sizes;          // heap-allocated array of sizes
    hsva_t** buffers;        // heap-allocated array of pointers
};

struct Program {
    float duration;
    uint8_t layer_count;
    uint8_t max_remap_length;

    LayerDef* layers;        // heap-allocated array
    BufferPool pool;
    hsva_t* temp_buffer;     // heap-allocated, size = max_remap_length
};

static constexpr uint8_t BLOB_VERSION = 2;
static constexpr uint8_t SOURCE_NONE = 0xFF;
```

---

## Animation Instantiation

The blob stores animation parameters as a tagged union (`AnimParams`). At
runtime, the engine creates polymorphic `Animation*` subclasses via a factory
function:

```cpp
Animation* create_animation(const AnimationEvent& e, Program* prog,
                            const LayerDef& dep_layer);
```

The factory dispatches on `AnimParams.type` and constructs the appropriate
subclass (`AnimWave`, `AnimSpark`, `AnimPaint`, `AnimShift`). Each subclass
has a virtual `render()` method. See `docs/engine.md` for details.

The decoder only deals with data — it decodes params into the tagged union.
The engine handles instantiation.

---

## Decode Flow

Single-pass decode with a `BlobReader` cursor:

```cpp
Program* decode_program(const uint8_t* blob, size_t len) {
    BlobReader r(blob, len);

    // Header: magic "ELEM", version, layer_count, buffer_count,
    //         max_remap_length, duration
    // ... validate header ...

    Program* prog = new Program();

    // Buffer pool: read sizes, allocate hsva_t[] for each
    for (uint8_t i = 0; i < buffer_count; i++) {
        uint8_t sz = r.read_u8();
        prog->pool.sizes[i] = sz;
        prog->pool.buffers[i] = new hsva_t[sz]();
    }

    // Temp buffer
    prog->temp_buffer = new hsva_t[max_remap_length];

    // Layers
    prog->layers = new LayerDef[layer_count]();
    for (uint8_t li = 0; li < layer_count; li++) {
        // Read index_map_length + index_map bytes
        // Read event_count
        // For each event:
        //   Read anim_type, t_start, duration, source_layer, remap_is_identity
        //   Read remap_length + remap bytes
        //   Read params_size, then dispatch to type-specific parser
    }

    return prog;

fail:
    free_program(prog);
    return nullptr;
}
```

Error handling: any parse failure jumps to `fail`, which calls `free_program()`
to clean up partial allocations. All pointers are initialized to `nullptr` so
`free_program()` can safely skip unallocated fields.

---

## free_program

Walks the entire structure tree and frees each allocation:

```cpp
void free_program(Program* prog) {
    if (!prog) return;

    if (prog->layers) {
        for (uint8_t li = 0; li < prog->layer_count; li++) {
            LayerDef& layer = prog->layers[li];
            if (layer.events) {
                for (uint16_t ei = 0; ei < layer.event_count; ei++) {
                    delete[] layer.events[ei].remap;
                    if (layer.events[ei].params.type == ANIM_PAINT)
                        delete[] layer.events[ei].params.paint.pixels;
                }
                delete[] layer.events;
            }
            delete[] layer.index_map;
            delete[] layer.buffer;   // engine-allocated, but freed here
        }
        delete[] prog->layers;
    }

    if (prog->pool.buffers) {
        for (uint8_t i = 0; i < prog->pool.count; i++)
            delete[] prog->pool.buffers[i];
        delete[] prog->pool.buffers;
    }
    delete[] prog->pool.sizes;
    delete[] prog->temp_buffer;

    delete prog;
}
```

Note: `layer.buffer` is allocated by the Engine (not the decoder), but freed
here since `free_program()` is the single teardown path (called by Engine's
destructor).

---

## Remap Storage

Each event has its own heap-allocated remap array. The `remap_is_identity` flag
tells the engine to skip scatter copy. When identity, the remap array is still
allocated and valid but the engine won't read it.

---

## Endianness

ESP32 is little-endian. Blob is little-endian. No byte swapping needed.
`memcpy` directly into float/uint fields.

Desktop (x86/x64) is also little-endian — tests work without conversion.

---

## Validation (at decode time)

Minimal — the Python compiler already validates. Safety checks at decode time:
- Magic == "ELEM"
- Version == 2
- Blob length sufficient for declared contents (checked before every read)
- `layer_count <= 32`
- `source_layer < layer_count` (when not `SOURCE_NONE`)
- `buffer_id < buffer_count` (for shift events)

Return `nullptr` on any failure.

---

## Testing (Catch2, desktop)

Test the decoder on desktop by:
1. Using the Python compiler to produce a known blob (`test/fixtures/generate.py`)
2. Loading it in C++ test, calling `decode_program()`
3. Verifying struct contents match expected values

Test fixture is auto-generated by CMake if missing. See `test/test_decoder.cpp`.
