#pragma once

#include <cstdint>

#include "pixel_view.h"
#include "strip.h"

// Compositor blends active dst PixelViews into the RGB Strip, bottom-to-top.
// Engine passes one entry per layer, with nullptr for inactive layers. Gamma
// and channel order are last-mile transforms applied after composite().

class Compositor {
public:
    Compositor() = default;

    // Clear `out`, then blend each non-null view into it in array order.
    void composite(Strip& out, PixelView* const* active_dst_views, uint8_t count);
};
