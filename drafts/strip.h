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
    Strip() = default;
    ~Strip();

    Strip(const Strip&) = delete;
    Strip& operator=(const Strip&) = delete;
    Strip(Strip&&) = delete;
    Strip& operator=(Strip&&) = delete;

    // Allocate exact RGB storage for this strip.
    //
    // This is called when the owner applies a HardwareProfile. It is not part
    // of the per-frame render path.
    bool resize(uint16_t size);

    // Release owned RGB storage.
    void reset();

    rgb_t& operator[](uint16_t idx);
    const rgb_t& operator[](uint16_t idx) const;

    uint16_t size() const;
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
    // Owned canonical RGB pixel storage.
    rgb_t* _pixels = nullptr;

    // Number of logical output pixels.
    uint16_t _size = 0;
};

// Apply gamma correction in place to the current RGB frame.
//
// After this call the strip still contains RGB pixels, but no longer linear
// canonical RGB. It is intended as a last-mile output transform.
void apply_gamma(Strip& strip);
