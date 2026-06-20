#include "gamma.h"

#include <cassert>
#include <cmath>

namespace {

static inline uint8_t clamp_to_u8(float value)
{
    if (value < 0.0) return 0;
    if (value > 255.0) return 255;
    return static_cast<uint8_t>(value);
}

}  // namespace

void GammaCorrection::set_identity()
{
    for (uint16_t i = 0; i < 256; i++) {
        _lut[i] = static_cast<uint8_t>(i);
    }
    _gamma = IDENTITY_GAMMA;
}

void GammaCorrection::set_gamma(float gamma)
{
    assert(!std::isnan(gamma));

    if (gamma == IDENTITY_GAMMA) {
        set_identity();
        return;
    }

    if (gamma <= IDENTITY_GAMMA || gamma > MAX_SUPPORTED_GAMMA) {
        return;
    }

    for (uint16_t i = 0; i < 256; i++) {
        const float in = static_cast<float>(i) / 255.0;
        const float out = ::powf(in, gamma) * 255.0 + 0.5;
        _lut[i] = clamp_to_u8(out);
    }
    _gamma = gamma;
}
