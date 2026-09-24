#include "core/animations/shift.h"

#include "core/colors.h"
#include "core/pixel_view.h"

#include <cmath>
#include <new>

namespace {
bool read_finite_f32(BlobReader& r, float& out) {
    return r.read_f32_le(out) && std::isfinite(out);
}
}  // namespace

Shift::Shift(const ShiftParams& p) : _p(p) {}

void Shift::initialize(const PixelView* src, PixelView* work)
{
    _work = work;
    if (work == nullptr || src == nullptr) {
        return;
    }
    const uint16_t n = src->size() < work->size() ? src->size() : work->size();
    for (uint16_t i = 0; i < n; i++) {
        (*work)[i] = (*src)[i];
    }
}

void Shift::render(PixelView& dst, ProgramDuration t)
{
    if (_work == nullptr) {
        return;
    }

    const hsva_t fill(_p.fill_h, _p.fill_s, _p.fill_v, _p.fill_a);
    const int work_len = static_cast<int>(_work->size());

    // The raw offset grows without bound over a long event, so bring it
    // into a small range in double before the per-pixel float math.
    double offset_d = static_cast<double>(_p.velocity)
                    * static_cast<double>(t.ms) / 1000.0;
    if (_p.direction == 0) {
        offset_d = -offset_d;
    }
    if (_p.circular) {
        // Whole turns change nothing: normalize to [0, work_len).
        offset_d = std::fmod(offset_d, static_cast<double>(work_len));
        if (offset_d < 0.0) {
            offset_d += work_len;
        }
    } else if (std::fabs(offset_d) > static_cast<double>(work_len) + dst.size()) {
        // Shifted wholly out of view: every pixel is exposed.
        for (uint16_t i = 0; i < dst.size(); i++) {
            dst[i] = fill;
        }
        return;
    }
    const float offset = static_cast<float>(offset_d);

    for (uint16_t i = 0; i < dst.size(); i++) {
        const float src_f = static_cast<float>(i) - offset;
        int src_i = static_cast<int>(std::floor(src_f));

        if (_p.circular) {
            src_i = ((src_i % work_len) + work_len) % work_len;
            dst[i] = (*_work)[static_cast<uint16_t>(src_i)];
        } else if (src_i >= 0 && src_i < work_len) {
            dst[i] = (*_work)[static_cast<uint16_t>(src_i)];
        } else {
            dst[i] = fill;
        }
    }
}

Animation* Shift::from_blob(const uint8_t* params, size_t params_size,
                                 DecodeError* err_out)
{
    *err_out = DecodeError::InvalidField;
    BlobReader r(params, params_size);
    ShiftParams p;

    if (!r.read_u8(p.direction)) return nullptr;
    if (p.direction > 1)         return nullptr;
    if (!read_finite_f32(r, p.velocity)) return nullptr;
    if (!r.read_u8(p.circular))  return nullptr;
    if (p.circular > 1)          return nullptr;
    if (!read_finite_f32(r, p.fill_h)) return nullptr;
    if (!read_finite_f32(r, p.fill_s)) return nullptr;
    if (!read_finite_f32(r, p.fill_v)) return nullptr;
    if (!read_finite_f32(r, p.fill_a)) return nullptr;

    Shift* anim = new (std::nothrow) Shift(p);
    if (anim == nullptr) {
        *err_out = DecodeError::OutOfMemory;
        return nullptr;
    }
    *err_out = DecodeError::Ok;
    return anim;
}
