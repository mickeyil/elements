#include "gamma.h"

#include <cmath>

namespace {

static inline uint8_t clamp_to_u8(float value)
{
    if (!(value > 0.0f)) {
        return 0;
    }
    if (!(value < 255.0f)) {
        return 255;
    }
    return static_cast<uint8_t>(value);
}

}  // namespace

GammaCorrection::GammaCorrection()
{
    set_identity();
}

void GammaCorrection::set_identity()
{
    for (uint16_t i = 0; i < 256; i++) {
        _lut[i] = static_cast<uint8_t>(i);
    }
    _gamma = kIdentityGamma;
}

void GammaCorrection::set_gamma(float gamma)
{
    if (gamma == kIdentityGamma) {
        set_identity();
        return;
    }

    if (!(gamma > kIdentityGamma && gamma <= kMaxSupportedGamma)) {
        return;
    }

    for (uint16_t i = 0; i < 256; i++) {
        const float in = static_cast<float>(i) / 255.0f;
        const float out = ::powf(in, gamma) * 255.0f + 0.5f;
        _lut[i] = clamp_to_u8(out);
    }
    _gamma = gamma;
}

rgb_t GammaCorrection::correct(const rgb_t& color) const
{
    return rgb_t(_lut[color.r], _lut[color.g], _lut[color.b]);
}

float GammaCorrection::gamma() const
{
    return _gamma;
}
