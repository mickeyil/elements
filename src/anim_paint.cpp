#include "anim_paint.h"

#include "pixel_view.h"

#include <cmath>
#include <new>

namespace {
bool read_finite_f32(BlobReader& r, float& out) {
    return r.read_f32_le(out) && std::isfinite(out);
}
}  // namespace

AnimPaint::AnimPaint(float h, float s, float v, float a)
    : _mode(Mode::Solid), _solid(h, s, v, a), _constant(nullptr), _constant_count(0) {}

AnimPaint::AnimPaint(hsva_t* constant, uint8_t count)
    : _mode(Mode::Constant), _solid(), _constant(constant), _constant_count(count) {}

AnimPaint::~AnimPaint() { delete[] _constant; }

void AnimPaint::render(PixelView& dst, float)
{
    if (_mode == Mode::Solid) {
        for (uint16_t i = 0; i < dst.size(); i++) {
            dst[i] = _solid;
        }
        return;
    }

    // Constant mode: the decoder pinned _constant_count == dst.size().
    for (uint16_t i = 0; i < dst.size(); i++) {
        dst[i] = _constant[i];
    }
}

Animation* AnimPaint::from_blob(const uint8_t* params, size_t params_size,
                                 DecodeError* err_out)
{
    *err_out = DecodeError::InvalidField;
    BlobReader r(params, params_size);

    uint8_t mode = 0;
    if (!r.read_u8(mode)) return nullptr;

    if (mode == 0) {
        float h, s, v, a;
        if (!read_finite_f32(r, h)) return nullptr;
        if (!read_finite_f32(r, s)) return nullptr;
        if (!read_finite_f32(r, v)) return nullptr;
        if (!read_finite_f32(r, a)) return nullptr;

        AnimPaint* anim = new (std::nothrow) AnimPaint(h, s, v, a);
        if (anim == nullptr) {
            *err_out = DecodeError::OutOfMemory;
            return nullptr;
        }
        *err_out = DecodeError::Ok;
        return anim;
    }

    if (mode == 1) {
        uint8_t count = 0;
        if (!r.read_u8(count)) return nullptr;

        hsva_t* constant = nullptr;
        if (count > 0) {
            constant = new (std::nothrow) hsva_t[count];
            if (constant == nullptr) {
                *err_out = DecodeError::OutOfMemory;
                return nullptr;
            }
            for (uint8_t i = 0; i < count; i++) {
                if (!read_finite_f32(r, constant[i].h) ||
                    !read_finite_f32(r, constant[i].s) ||
                    !read_finite_f32(r, constant[i].v) ||
                    !read_finite_f32(r, constant[i].a)) {
                    delete[] constant;
                    return nullptr;
                }
            }
        }

        AnimPaint* anim = new (std::nothrow) AnimPaint(constant, count);
        if (anim == nullptr) {
            delete[] constant;
            *err_out = DecodeError::OutOfMemory;
            return nullptr;
        }
        *err_out = DecodeError::Ok;
        return anim;
    }

    return nullptr;  // unrecognized mode
}
