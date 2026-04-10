#include "strip.h"

#include <cstring>

namespace {

static inline rgb_t reorder_rgb(const rgb_t& src, ColorOrder order)
{
    switch (order) {
        case ColorOrder::RGB: return rgb_t(src.r, src.g, src.b);
        case ColorOrder::GRB: return rgb_t(src.g, src.r, src.b);
        case ColorOrder::BGR: return rgb_t(src.b, src.g, src.r);
        case ColorOrder::BRG: return rgb_t(src.b, src.r, src.g);
        case ColorOrder::GBR: return rgb_t(src.g, src.b, src.r);
        case ColorOrder::RBG: return rgb_t(src.r, src.b, src.g);
    }
    return src;
}

}  // namespace

void Strip::rebind(rgb_t* pixels, uint8_t size)
{
    _pixels = pixels;
    _size = size;
}

rgb_t& Strip::operator[](uint8_t idx)
{
    return _pixels[idx];
}

const rgb_t& Strip::operator[](uint8_t idx) const
{
    return _pixels[idx];
}

uint8_t Strip::size() const
{
    return _size;
}

bool Strip::empty() const
{
    return _size == 0;
}

size_t Strip::byte_size() const
{
    return static_cast<size_t>(_size) * sizeof(rgb_t);
}

void Strip::clear()
{
    if (_pixels == nullptr || _size == 0) {
        return;
    }
    std::memset(_pixels, 0, byte_size());
}

rgb_t* Strip::pixels()
{
    return _pixels;
}

const rgb_t* Strip::pixels() const
{
    return _pixels;
}

uint8_t* Strip::bytes()
{
    return reinterpret_cast<uint8_t*>(_pixels);
}

const uint8_t* Strip::bytes() const
{
    return reinterpret_cast<const uint8_t*>(_pixels);
}

void Strip::copy_to(uint8_t* dst, ColorOrder order) const
{
    if (dst == nullptr || _pixels == nullptr) {
        return;
    }

    for (uint8_t i = 0; i < _size; i++) {
        const rgb_t out = reorder_rgb(_pixels[i], order);
        dst[i * 3 + 0] = out.r;
        dst[i * 3 + 1] = out.g;
        dst[i * 3 + 2] = out.b;
    }
}

void apply_gamma(Strip& strip)
{
    for (uint8_t i = 0; i < strip.size(); i++) {
        strip[i] = gamma_correct(strip[i]);
    }
}
