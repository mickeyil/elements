#include "core/colors.h"
#include <cmath>

rgb_t hsv_to_rgb(float h, float s, float v)
{
    // Normalize hue to 0-360
    h = fmod(h, 360.0f);
    if (h < 0) h += 360.0f;

    float r, g, b;

    if (s <= 0.0f) {
        r = g = b = v;
    } else {
        float hh = h / 60.0f;
        int i = (int)hh;
        float ff = hh - i;
        float p = v * (1.0f - s);
        float q = v * (1.0f - s * ff);
        float t = v * (1.0f - s * (1.0f - ff));

        switch (i) {
            case 0:  r = v; g = t; b = p; break;
            case 1:  r = q; g = v; b = p; break;
            case 2:  r = p; g = v; b = t; break;
            case 3:  r = p; g = q; b = v; break;
            case 4:  r = t; g = p; b = v; break;
            default: r = v; g = p; b = q; break;
        }
    }

    // Convert to 0-255 (linear, no gamma)
    uint8_t r8 = (uint8_t)(r * 255.0f + 0.5f);
    uint8_t g8 = (uint8_t)(g * 255.0f + 0.5f);
    uint8_t b8 = (uint8_t)(b * 255.0f + 0.5f);

    return rgb_t(r8, g8, b8);
}

rgb_t rgb_alpha_blend(const rgb_t& bg, const rgb_t& fg, float alpha)
{
    if (alpha <= 0.0f) return bg;
    if (alpha >= 1.0f) return fg;
    return rgb_t(
        (uint8_t)(bg.r + (fg.r - bg.r) * alpha + 0.5f),
        (uint8_t)(bg.g + (fg.g - bg.g) * alpha + 0.5f),
        (uint8_t)(bg.b + (fg.b - bg.b) * alpha + 0.5f)
    );
}
