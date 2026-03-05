#pragma once

#include "strip.h"
#include "decoder.h"

class Compositor {
public:
    Compositor(Strip& strip, bool gamma_enabled);

    // Blend active layers into the strip.
    // Layers are composited in index order (0 = bottom, N-1 = top).
    void composite(LayerDef* layers, uint8_t count, uint32_t active_mask);

private:
    Strip& _strip;
    bool _gamma_enabled;
};
