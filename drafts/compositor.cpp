#include "compositor.h"

void Compositor::composite(Strip& out, PixelView* const* active_dst_views, uint8_t count)
{
    out.clear();

    for (uint8_t li = 0; li < count; li++) {
        PixelView* view = active_dst_views[li];
        if (view == nullptr) {
            continue;
        }

        // Defensive guard. Decoder validation should ensure only dst views,
        // which have physical mapping, can reach the compositor.
        if (!view->has_physical_mapping()) {
            continue;
        }

        for (uint16_t i = 0; i < view->size(); i++) {
            const hsva_t& px = (*view)[i];
            if (px.a <= 0.0f) {
                continue;
            }

            const uint16_t phys = view->physical_index(i);
            const rgb_t fg = hsv_to_rgb(px.h, px.s, px.v);

            if (px.a >= 1.0f) {
                out[phys] = fg;
            } else {
                out[phys] = rgb_alpha_blend(out[phys], fg, px.a);
            }
        }
    }

    // TODO: if lower-layer conversion/blending cost ever matters, this is the
    // place for an occlusion optimization that skips work on fully hidden
    // pixels. The first-pass design stays simple and correct.
}
