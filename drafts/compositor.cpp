#include "compositor.h"

void Compositor::composite(Strip& out, Layer* layers, uint8_t count, uint32_t active_mask)
{
    out.clear();

    for (uint8_t li = 0; li < count; li++) {
        if (!(active_mask & (1u << li))) {
            continue;
        }

        Layer& layer = layers[li];
        for (uint16_t i = 0; i < layer.buffer_length; i++) {
            const hsva_t& px = layer.buffer[i];
            if (px.a <= 0.0f) {
                continue;
            }

            const uint16_t phys = layer.physical_map[i];
            const rgb_t fg = hsv_to_rgb(px.h, px.s, px.v);

            if (px.a >= 1.0f) {
                out[phys] = fg;
            } else {
                out[phys] = rgb_lerp(out[phys], fg, px.a);
            }
        }
    }

    // TODO: if lower-layer conversion/blending cost ever matters, this is the
    // place for an occlusion optimization that skips work on fully hidden
    // pixels. The first-pass design stays simple and correct.
}
