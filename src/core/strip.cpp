#include "core/strip.h"

#include <cstring>
#include <new>

Strip::~Strip()
{
    reset();
}

bool Strip::resize(uint16_t size)
{
    reset();

    if (size == 0) {
        return true;
    }

    _pixels = new (std::nothrow) rgb_t[size]();
    if (_pixels == nullptr) {
        return false;
    }

    _size = size;
    return true;
}

void Strip::reset()
{
    delete[] _pixels;
    _pixels = nullptr;
    _size = 0;
}

void Strip::clear()
{
    if (_pixels == nullptr) {
        return;
    }
    std::memset(_pixels, 0, byte_size());
}

void Strip::copy_to(uint8_t* dst, uint16_t dst_pixels, ColorOrder order,
                    const GammaCorrection& gamma) const
{
    if (dst == nullptr) {
        return;
    }

    const uint16_t copy_count = _size < dst_pixels ? _size : dst_pixels;

    for (uint16_t i = 0; i < copy_count; i++) {
        const rgb_t src = gamma.correct(_pixels[i]);

        if (order == ColorOrder::BGR) {
            dst[i * 3 + 0] = src.b;
            dst[i * 3 + 1] = src.g;
            dst[i * 3 + 2] = src.r;
        } else {
            dst[i * 3 + 0] = src.r;
            dst[i * 3 + 1] = src.g;
            dst[i * 3 + 2] = src.b;
        }
    }

    if (dst_pixels > copy_count) {
        std::memset(dst + copy_count * 3, 0, (dst_pixels - copy_count) * 3);
    }
}
