#pragma once

#include <cstdint>
#include <cstddef>
#include "colors.h"

// ---------------------------------------------------------------------------
// Animation type IDs (match blob format)
// ---------------------------------------------------------------------------

enum AnimType : uint8_t {
    ANIM_WAVE  = 0,
    ANIM_SHIFT = 1,
    ANIM_SPARK = 2,
    ANIM_FILL  = 3,
};

// ---------------------------------------------------------------------------
// Animation params — tagged union
// ---------------------------------------------------------------------------

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

struct FillParams {
    float color_h, color_s, color_v;
};

struct AnimParams {
    AnimType type;
    union {
        WaveParams  wave;
        ShiftParams shift;
        SparkParams spark;
        FillParams  fill;
    };
};

// ---------------------------------------------------------------------------
// Program structs
// ---------------------------------------------------------------------------

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
    hsva_t** buffers;        // heap-allocated array of pointers to heap-allocated hsva_t arrays
};

struct Program {
    float duration;
    uint8_t layer_count;
    uint8_t max_remap_length;

    LayerDef* layers;        // heap-allocated array
    BufferPool pool;
    hsva_t* temp_buffer;     // heap-allocated, size = max_remap_length
};

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------

static constexpr uint8_t BLOB_VERSION = 2;
static constexpr uint8_t SOURCE_NONE = 0xFF;

/// Decode a binary blob into a Program. Returns nullptr on failure.
Program* decode_program(const uint8_t* blob, size_t len);

/// Free all memory associated with a Program.
void free_program(Program* prog);
