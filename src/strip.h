#pragma once

#include <cstddef>
#include <cstdint>

#include "colors.h"
#include "gamma.h"
#include "hardware_profile.h"

// Strip is the canonical linear-RGB frame buffer. Sim, tests, and debug
// paths read it directly; the LED output reads it via copy_to().
//
// Owns pixel storage. Allocate via resize(); release via reset() or
// destruction.

// `bytes()` and `copy_to()` walk the buffer as packed 3-byte triples.
static_assert(sizeof(rgb_t) == 3, "Strip assumes packed rgb_t storage");

class Strip {
public:
    Strip() = default;
    ~Strip();

    Strip(const Strip&) = delete;
    Strip& operator=(const Strip&) = delete;
    Strip(Strip&&) = delete;
    Strip& operator=(Strip&&) = delete;

    // Allocate storage for `size` pixels (zeroed). Returns false on allocation
    // failure; the strip is left empty.
    bool resize(uint16_t size);

    // Release owned pixel storage.
    void reset();

    // Zero every pixel.
    void clear();

    rgb_t& operator[](uint16_t idx) { return _pixels[idx]; }
    const rgb_t& operator[](uint16_t idx) const { return _pixels[idx]; }

    uint16_t size() const { return _size; }
    bool empty() const { return _size == 0; }
    size_t byte_size() const { return static_cast<size_t>(_size) * sizeof(rgb_t); }

    rgb_t* pixels() { return _pixels; }
    const rgb_t* pixels() const { return _pixels; }

    uint8_t* bytes() { return reinterpret_cast<uint8_t*>(_pixels); }
    const uint8_t* bytes() const { return reinterpret_cast<const uint8_t*>(_pixels); }

    // Copy the frame into `dst` using `order` for channel layout. Writes
    // exactly `dst_pixels * 3` bytes -- the tail past size() is zeroed.
    void copy_to(uint8_t* dst, uint16_t dst_pixels, ColorOrder order) const;

private:
    rgb_t* _pixels = nullptr;
    uint16_t _size = 0;
};

// Apply gamma in place. Default-constructed GammaCorrection is identity,
// so the call is safe on sim/test/debug paths without a guard.
void apply_gamma(Strip& strip, const GammaCorrection& gamma);
