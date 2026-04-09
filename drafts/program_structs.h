#pragma once

// Draft-only API.
//
// Proposed decoded-program structures for the PixelBufferPool + PixelView
// redesign. These are intentionally isolated from the current runtime code so
// the design can be refined without touching build-integrated headers.

#include <cstdint>

#include "animation.h"
#include "colors.h"
#include "pixel_buffer_pool.h"
#include "pixel_view.h"

static constexpr uint16_t PIXV_NONE = 0xFFFF;
static constexpr uint16_t PIXBUF_NONE = 0xFFFF;

struct PixelViewDef {
    // Which real PixelBufferPool buffer this view is built on top of.
    // This is blob/decode metadata. Animations never see raw buffer indices.
    uint16_t buffer_idx = PIXBUF_NONE;

    // Number of logical pixels exposed through the view.
    uint8_t size = 0;

    // True when the view maps directly onto backing buffer slots [0..size).
    bool is_identity = true;

    // Blob-decoded logical->buffer-slot mapping.
    // Null when `is_identity == true`.
    uint16_t* indices = nullptr;
};

struct AnimationEvent {
    // Decoder-constructed animation instance for this event.
    Animation* animation = nullptr;

    // Start time in seconds from program start.
    float t_start = 0.0f;

    // Duration in seconds.
    float duration = 0.0f;

    // Optional source PixelView used during initialize().
    // Events always reference PixelView indices, not PixelBufferPool indices.
    uint16_t src_pixv_idx = PIXV_NONE;

    // Required destination PixelView used during render().
    // dst is the only mandatory view reference on the event.
    uint16_t dst_pixv_idx = PIXV_NONE;

    // Optional persistent work PixelView for stateful animations.
    // Compiler is responsible for assigning work storage whose lifetime and
    // reuse are valid for the animation type.
    uint16_t work_pixv_idx = PIXV_NONE;
};

struct LayerDef {
    // Which real pool buffer is the canonical compositing buffer for this layer.
    uint16_t buffer_idx = PIXBUF_NONE;

    // Resolved pointer to the canonical layer buffer.
    // The compositor reads this canonical buffer directly; it does not operate
    // on arbitrary PixelViews.
    hsva_t* buffer = nullptr;

    // Number of canonical compositing slots in `buffer`.
    uint8_t buffer_length = 0;

    // Maps canonical layer buffer slot -> physical LED index.
    uint8_t* physical_map = nullptr;

    // Number of events on this layer. Events are time-sorted and non-overlapping.
    uint16_t event_count = 0;

    // Decoded event array for this layer.
    AnimationEvent* events = nullptr;
};

struct Program {
    // Total program duration in seconds.
    float duration = 0.0f;

    // Number of composited layers.
    uint8_t layer_count = 0;

    // Bottom-to-top compositing order.
    LayerDef* layers = nullptr;

    // Owns all real hsva_t backing buffers.
    PixelBufferPool pixel_buffer_pool;

    // Number of resolved logical PixelViews.
    uint16_t pixel_view_count = 0;

    // Resolved logical PixelView table. Events reference indices into this
    // table, and each view is already bound to a real pool buffer.
    PixelView* pixel_views = nullptr;

    // Blob-decoded PixelView descriptors retained for cleanup.
    //
    // In the current draft design, PixelView borrows def.indices directly, so
    // these descriptors must remain alive for the lifetime of the Program
    // unless the final decoder copies index arrays elsewhere.
    PixelViewDef* pixel_view_defs = nullptr;
};

// Build resolved Program state from blob-decoded buffer sizes and view defs.
//
// TODO: final decoder integration will likely fold this into decode_program().
bool initialize_program_views(
    Program& prog,
    const uint16_t* buffer_sizes,
    uint16_t buffer_count
);

// Free all owned memory associated with Program.
void free_program_sketch(Program* prog);
