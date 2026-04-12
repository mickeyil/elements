#pragma once

// Draft-only API.
//
// Layer is a visual timeline only.
//
// It no longer owns or borrows a canonical compositing buffer. All HSVA storage
// is owned by PixelBufferPool and reached through PixelViews referenced by
// events. The compositor receives active dst PixelViews directly from Engine.

#include <cstdint>

#include "animation.h"
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
    //
    // dst views must be compositable: PixelView::has_physical_mapping() should
    // be true so the compositor can route rendered pixels to physical LEDs.
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

    // Adopt the owned event array produced by the decoder.
    void initialize(AnimationEvent* events, uint16_t event_count);

    // Release owned event state.
    void reset();

    // Number of decoded events on this layer.
    uint16_t event_count = 0;

    // Owned decoded event array.
    AnimationEvent* events = nullptr;
};
