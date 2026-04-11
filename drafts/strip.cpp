#include "strip.h"

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
        _size = 0;
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

rgb_t& Strip::operator[](uint16_t idx)
{
    return _pixels[idx];
}

const rgb_t& Strip::operator[](uint16_t idx) const
{
    return _pixels[idx];
}

uint16_t Strip::size() const
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

    for (uint16_t i = 0; i < _size; i++) {
        const rgb_t& src = _pixels[i];

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
}

void apply_gamma(Strip& strip, const GammaCorrection& gamma)
{
    for (uint16_t i = 0; i < strip.size(); i++) {
        strip[i] = gamma.correct(strip[i]);
    }
}
