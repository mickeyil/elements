#pragma once

// Draft-only API.
//
// Strip is the canonical final RGB frame buffer produced by the render core.
//
// Design intent:
// - rendering writes canonical linear RGB into Strip
// - simulator/tests/stdout can inspect Strip directly
// - hardware output may apply last-mile transforms afterward

#include <cstddef>
#include <cstdint>

#include "colors.h"
#include "hardware_profile.h"

static_assert(sizeof(rgb_t) == 3,
              "Strip assumes packed rgb_t storage");

class Strip {
public:
    Strip(rgb_t* pixels = nullptr, uint8_t size = 0)
        : _pixels(pixels), _size(size) {}

    void rebind(rgb_t* pixels, uint8_t size);

    rgb_t& operator[](uint8_t idx);
    const rgb_t& operator[](uint8_t idx) const;

    uint8_t size() const;
    bool empty() const;
    size_t byte_size() const;

    void clear();

    rgb_t* pixels();
    const rgb_t* pixels() const;

    uint8_t* bytes();
    const uint8_t* bytes() const;

    // Copy the canonical RGB frame into a hardware-facing byte buffer using
    // the requested output channel order. No gamma correction is applied here.
    void copy_to(uint8_t* dst, ColorOrder order) const;

private:
    // Non-owning canonical RGB pixel storage.
    rgb_t* _pixels = nullptr;

    // Number of logical output pixels.
    uint8_t _size = 0;
};

// Apply gamma correction in place to the current RGB frame.
//
// After this call the strip still contains RGB pixels, but no longer linear
// canonical RGB. It is intended as a last-mile output transform.
void apply_gamma(Strip& strip);
