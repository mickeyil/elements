#pragma once

#include <cstdint>

#include "core/animation.h"
#include "core/runtime_constants.h"

// Layer is one bottom-to-top visual timeline. It owns its event array and the
// Animation instance attached to each event; the array is decoder-allocated
// and adopted via initialize(). At most one event is active at any time.
//
// Layer carries no pixel storage -- HSVA buffers live in PixelBufferPool and
// are reached through PixelViews referenced by event indices.

struct AnimationEvent {
    Animation* animation = nullptr;

    // Program-relative seconds.
    float start = 0.0f;
    float duration = 0.0f;

    // PixelView indices. dst is required and must carry a physical mapping
    // (the compositor consumes it). src and work are optional.
    uint16_t src_pixv_idx = PIXV_NONE;
    uint16_t dst_pixv_idx = PIXV_NONE;
    uint16_t work_pixv_idx = PIXV_NONE;
};

class Layer {
public:
    ~Layer();

    // Adopt the event array. Layer takes ownership; reset/destruction frees the
    // array and each event's animation.
    void initialize(AnimationEvent* events, uint16_t count);

    // Release owned event state.
    void reset();

    uint16_t count() const { return _count; }

    // Look up an event by index. No bounds check; caller must use idx < count().
    AnimationEvent& at(uint16_t idx) { return _events[idx]; }
    const AnimationEvent& at(uint16_t idx) const { return _events[idx]; }

    // Find the event active at program-relative time `t`. Returns nullptr if
    // no event covers `t`. Half-open interval: start <= t < start + duration.
    const AnimationEvent* active_at(float t) const;

private:
    AnimationEvent* _events = nullptr;
    uint16_t _count = 0;
};
