#pragma once

// Draft-only API.
//
// Layer is the runtime canonical compositing layer. It owns its event list and
// physical mapping, but borrows its HSVA backing buffer from PixelBufferPool.

#include <cstdint>

#include "animation.h"
#include "colors.h"
#include "runtime_constants.h"

struct AnimationEvent {
    // Decoder-constructed animation instance for this event.
    Animation* animation = nullptr;

    // Start time in seconds from program start.
    float t_start = 0.0f;

    // Duration in seconds.
    float duration = 0.0f;

    // Optional source PixelView used during initialize().
    uint16_t src_pixv_idx = PIXV_NONE;

    // Required destination PixelView used during render().
    uint16_t dst_pixv_idx = PIXV_NONE;

    // Optional persistent work PixelView for stateful animations.
    uint16_t work_pixv_idx = PIXV_NONE;
};

struct Layer {
    Layer() = default;
    ~Layer();

    Layer(const Layer&) = delete;
    Layer& operator=(const Layer&) = delete;
    Layer(Layer&&) = delete;
    Layer& operator=(Layer&&) = delete;

    // Bind the runtime layer to its resolved canonical buffer and adopt the
    // owned event/mapping arrays produced by the decoder.
    //
    // `buffer_length` is expected to come from the resolved PixelBufferPool
    // size for the canonical layer buffer, not from a second blob-owned
    // source of truth.
    void initialize(
        hsva_t* buffer,
        uint8_t buffer_length,
        uint8_t* physical_map,
        AnimationEvent* events,
        uint16_t event_count
    );

    // Release owned event/mapping state and clear the runtime binding.
    void reset();

    // Non-owning canonical layer buffer used by compositor.
    hsva_t* buffer = nullptr;

    // Number of canonical compositing slots in `buffer`.
    uint8_t buffer_length = 0;

    // Owned map from canonical layer slot -> physical LED index.
    uint8_t* physical_map = nullptr;

    // Number of decoded events on this layer.
    uint16_t event_count = 0;

    // Owned decoded event array.
    AnimationEvent* events = nullptr;
};
