#pragma once

#include <cstdint>
#include <type_traits>

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

// PixelBufferPool slices on hsva_t boundaries inside a contiguous array;
// the type must stay a flat trivially-copyable value so size and alignment
// stay predictable.
static_assert(sizeof(hsva_t) == 4 * sizeof(float),
              "hsva_t must remain exactly 4 floats");
static_assert(alignof(hsva_t) == alignof(float),
              "hsva_t alignment changed unexpectedly");
static_assert(std::is_standard_layout<hsva_t>::value,
              "hsva_t must remain standard-layout");
static_assert(std::is_trivially_copyable<hsva_t>::value,
              "hsva_t must remain trivially copyable");

// Output color: 8-bit RGB
struct rgb_t {
    uint8_t r, g, b;

    rgb_t() : r(0), g(0), b(0) {}
    rgb_t(uint8_t r, uint8_t g, uint8_t b) : r(r), g(g), b(b) {}
};

// Convert HSV (h: 0-360, s: 0-1, v: 0-1) to linear RGB. Hue wraps; s and
// v are clamped to 0-1, and NaN in any channel counts as 0.
rgb_t hsv_to_rgb(float h, float s, float v);

// Convert linear RGB to HSV; alpha is set to 1. Gray inputs get h=0, s=0.
hsva_t rgb_to_hsv(const rgb_t& c);

// Blend `fg` over `bg` by `alpha` in [0,1]: bg*(1-alpha) + fg*alpha.
rgb_t rgb_alpha_blend(const rgb_t& bg, const rgb_t& fg, float alpha);
