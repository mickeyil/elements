#pragma once

// Draft-only API.
//
// GammaCorrection owns a small 8-bit lookup table used as a final output
// transform after rendering has produced canonical RGB.
//
// Design intent:
// - default construction is safe and produces identity gamma
// - ESP/firmware can set a real LED gamma once from its hardware profile
// - sim/tests/stdout can keep the identity table and use the same output path
// - invalid gamma input leaves the previous valid table unchanged

#include <cstdint>

#include "colors.h"

static constexpr float kIdentityGamma = 1.0f;
static constexpr float kMaxSupportedGamma = 5.0f;

// Starting point for WS2812-style LEDs in dark rooms. The current fixed table
// in src/colors.cpp is equivalent to gamma 2.8.
static constexpr float kWs2812DarkRoomGamma = 2.8f;

class GammaCorrection {
public:
    GammaCorrection();

    // Reset to a trivial LUT: 0->0, 1->1, ..., 255->255.
    //
    // This is intentionally direct assignment, not powf(x, 1.0f), so identity
    // output has no floating-point rounding surprises.
    void set_identity();

    // Replace the LUT with a generated gamma curve.
    //
    // Accepted values:
    // - exactly 1.0: identity
    // - > 1.0 and <= kMaxSupportedGamma: generated LED correction curve
    //
    // Rejected values leave the object unchanged. This keeps a previously valid
    // output profile stable if a future/malformed hardware profile carries an
    // unusable gamma value.
    void set_gamma(float gamma);

    rgb_t correct(const rgb_t& color) const;

    float gamma() const;

private:
    // Lookup table for one 8-bit channel.
    uint8_t _lut[256];

    // Last accepted gamma value represented by _lut.
    float _gamma = kIdentityGamma;
};
