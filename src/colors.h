#pragma once

#include <cstdint>

// Internal color: HSV + Alpha, all floats.
// H: 0-360 (degrees, wraps), S: 0-1, V: 0-1, A: 0-1
struct hsva_t {
    float h;
    float s;
    float v;
    float a;

    hsva_t() : h(0), s(0), v(0), a(0) {}
    hsva_t(float h, float s, float v, float a = 1.0f)
        : h(h), s(s), v(v), a(a) {}
};

// Output color: 8-bit RGB
struct rgb_t {
    uint8_t r, g, b;

    rgb_t() : r(0), g(0), b(0) {}
    rgb_t(uint8_t r, uint8_t g, uint8_t b) : r(r), g(g), b(b) {}
};

// Convert HSV (h: 0-360, s: 0-1, v: 0-1) to gamma-corrected RGB
rgb_t hsv_to_rgb(float h, float s, float v);

// Linearly interpolate two RGB colors
rgb_t rgb_lerp(const rgb_t& a, const rgb_t& b, float t);
