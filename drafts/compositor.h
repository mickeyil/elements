#pragma once

// Draft-only API.
//
// Compositor merges canonical HSVA layer buffers into a final RGB Strip.
// Gamma correction and hardware channel reordering are intentionally outside
// this class; they are last-mile output transforms.

#include <cstdint>

#include "layer.h"
#include "strip.h"

class Compositor {
public:
    Compositor() = default;

    // Blend active layers bottom-to-top into the final RGB output strip.
    void composite(Strip& out, Layer* layers, uint8_t count, uint32_t active_mask);
};
