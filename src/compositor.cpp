#include "compositor.h"

Compositor::Compositor(Strip& strip, bool gamma_enabled)
    : _strip(strip), _gamma_enabled(gamma_enabled) {}

void Compositor::composite(LayerDef* layers, uint8_t count, uint32_t active_mask)
{
    _strip.clear();

    for (uint8_t li = 0; li < count; li++) {
        if (!(active_mask & (1u << li))) continue;

        LayerDef& layer = layers[li];
        hsva_t* buf = layer.buffer;
        const uint8_t* idx_map = layer.index_map;

        for (uint8_t i = 0; i < layer.index_map_length; i++) {
            float a = buf[i].a;
            if (a <= 0.0f) continue;

            uint8_t phys = idx_map[i];
            rgb_t fg = hsv_to_rgb(buf[i].h, buf[i].s, buf[i].v);

            if (a >= 1.0f) {
                _strip.set_rgb(phys, fg);
            } else {
                rgb_t bg = _strip.get_rgb(phys);
                _strip.set_rgb(phys, rgb_alpha_blend(bg, fg, a));
            }
        }
    }

    // Gamma correction once, after all blending
    if (_gamma_enabled) {
        for (uint16_t i = 0; i < _strip.length(); i++) {
            _strip.set_rgb(i, gamma_correct(_strip.get_rgb(i)));
        }
    }
}
