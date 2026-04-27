#include "anim_wave.h"

#include "colors.h"
#include "pixel_view.h"

#include <cmath>
#include <new>

namespace {
bool read_finite_f32(BlobReader& r, float& out) {
    return r.read_f32_le(out) && std::isfinite(out);
}
}  // namespace

AnimWave::AnimWave(const WaveParams& p) : _p(p) {}

void AnimWave::render(PixelView& dst, float t_animation)
{
    const float base_phase =
        (2.0f * static_cast<float>(M_PI) * t_animation / _p.period) + _p.phase0;
    const float range = _p.max_val - _p.min_val;

    for (uint16_t i = 0; i < dst.size(); i++) {
        const float phase = base_phase + i * _p.pixel_step;
        const float val = _p.min_val + range * (sinf(phase) * 0.5f + 0.5f);

        float h = _p.h, s = _p.s, v = _p.v;
        switch (_p.channel) {
            case 0: h = val; break;
            case 1: s = val; break;
            case 2: v = val; break;
        }
        dst[i] = hsva_t(h, s, v, 1.0f);
    }
}

Animation* AnimWave::from_blob(const uint8_t* params, size_t params_size,
                                DecodeError* err_out)
{
    *err_out = DecodeError::InvalidField;
    BlobReader r(params, params_size);
    WaveParams p;

    if (!r.read_u8(p.channel))           return nullptr;
    if (p.channel > 2)                    return nullptr;
    if (!read_finite_f32(r, p.h))         return nullptr;
    if (!read_finite_f32(r, p.s))         return nullptr;
    if (!read_finite_f32(r, p.v))         return nullptr;
    if (!read_finite_f32(r, p.min_val))   return nullptr;
    if (!read_finite_f32(r, p.max_val))   return nullptr;
    if (!read_finite_f32(r, p.period))    return nullptr;
    if (p.period <= 0.0f)                 return nullptr;
    if (!read_finite_f32(r, p.phase0))    return nullptr;
    if (!read_finite_f32(r, p.pixel_step)) return nullptr;

    AnimWave* anim = new (std::nothrow) AnimWave(p);
    if (anim == nullptr) {
        *err_out = DecodeError::OutOfMemory;
        return nullptr;
    }
    *err_out = DecodeError::Ok;
    return anim;
}
