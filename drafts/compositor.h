#pragma once

// Draft-only API.
//
// Compositor blends active destination PixelViews into a final RGB Strip.
//
// It does not know about Layer or AnimationEvent. Engine supplies one active
// dst view per layer, ordered bottom-to-top, and nullptr for inactive layers.
// Gamma correction and hardware channel reordering are intentionally outside
// this class; they are last-mile output transforms.

#include <cstdint>

#include "pixel_view.h"
#include "strip.h"

class Compositor {
public:
    Compositor() = default;

    // Blend active dst views bottom-to-top into the final RGB output strip.
    void composite(Strip& out, PixelView* const* active_dst_views, uint8_t count);
};
