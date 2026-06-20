#include "animations/spark.h"

#include "colors.h"
#include "pixel_view.h"

#include <cmath>
#include <new>

namespace {
bool read_finite_f32(BlobReader& r, float& out) {
    return r.read_f32_le(out) && std::isfinite(out);
}
}  // namespace

Spark::Spark(const SparkParams& p) : _p(p) {}

void Spark::render(PixelView& dst, float t_animation)
{
    float alpha;
    if (t_animation < _p.fade) {
        alpha = 1.0f - (t_animation / _p.fade);
        alpha = alpha * alpha;
    } else {
        alpha = 0.0f;
    }
    const hsva_t color(_p.color_h, _p.color_s, _p.color_v, alpha);
    for (uint16_t i = 0; i < dst.size(); i++) {
        dst[i] = color;
    }
}

Animation* Spark::from_blob(const uint8_t* params, size_t params_size,
                                 DecodeError* err_out)
{
    *err_out = DecodeError::InvalidField;
    BlobReader r(params, params_size);
    SparkParams p;

    if (!read_finite_f32(r, p.color_h)) return nullptr;
    if (!read_finite_f32(r, p.color_s)) return nullptr;
    if (!read_finite_f32(r, p.color_v)) return nullptr;
    if (!read_finite_f32(r, p.fade))    return nullptr;
    if (p.fade <= 0.0f)                  return nullptr;

    Spark* anim = new (std::nothrow) Spark(p);
    if (anim == nullptr) {
        *err_out = DecodeError::OutOfMemory;
        return nullptr;
    }
    *err_out = DecodeError::Ok;
    return anim;
}
