#pragma once

#include <cstdint>

#include "colors.h"

static constexpr float IDENTITY_GAMMA = 1.0f;
static constexpr float MAX_SUPPORTED_GAMMA = 5.0f;

// Recommended for WS2812 LEDs in dark settings.
static constexpr float DEFAULT_GAMMA = 2.8f;

class GammaCorrection {
public:
    GammaCorrection() { set_identity(); }

    // Reset to a trivial LUT: 0->0, 1->1, ..., 255->255.
    void set_identity();

    // Replace the LUT with a generated gamma curve.
    // Rejected (-inf, 1.0) and (MAX_SUPPORTED_GAMMA, inf) leave the object unchanged.
    void set_gamma(float gamma);

    // Apply the current LUT to each channel of `color`.
    rgb_t correct(const rgb_t& color) const {
        return rgb_t(_lut[color.r], _lut[color.g], _lut[color.b]);
    }

    // Last accepted gamma value represented by the LUT.
    float gamma() const { return _gamma; }

private:
    uint8_t _lut[256];
    float _gamma = IDENTITY_GAMMA;
};
