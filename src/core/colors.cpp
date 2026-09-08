#include "core/colors.h"
#include <cmath>

rgb_t hsv_to_rgb(float h, float s, float v)
{
    // Normalize hue to [0, 360). Adding 360 to a tiny negative hue rounds
    // to exactly 360, which would pick a sixth sector; fold it back to 0.
    // NaN lands there too.
    h = fmodf(h, 360.0f);
    if (h < 0.0f) h += 360.0f;
    if (!(h < 360.0f)) h = 0.0f;

    // Clamp s and v so the byte conversion below cannot leave 0-255.
    // NaN clamps to 0.
    if (!(s > 0.0f)) s = 0.0f; else if (s > 1.0f) s = 1.0f;
    if (!(v > 0.0f)) v = 0.0f; else if (v > 1.0f) v = 1.0f;

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

hsva_t rgb_to_hsv(const rgb_t& c)
{
    const float r = c.r / 255.0f;
    const float g = c.g / 255.0f;
    const float b = c.b / 255.0f;

    float max = r; if (g > max) max = g; if (b > max) max = b;
    float min = r; if (g < min) min = g; if (b < min) min = b;
    const float delta = max - min;

    const float v = max;
    if (delta <= 0.0f || max <= 0.0f) {
        return hsva_t(0.0f, 0.0f, v, 1.0f);
    }

    const float s = delta / max;

    float h;
    if (max == r) {
        h = 60.0f * ((g - b) / delta);
    } else if (max == g) {
        h = 60.0f * (2.0f + (b - r) / delta);
    } else {
        h = 60.0f * (4.0f + (r - g) / delta);
    }
    if (h < 0.0f) h += 360.0f;

    return hsva_t(h, s, v, 1.0f);
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
