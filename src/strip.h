#pragma once

#include <cstdint>
#include <cstring>
#include "colors.h"

// Thin wrapper around the physical LED output buffer (FastLED's CRGB array).
// Provides indexed RGB access. The compositor blends directly into this.

class Strip {
public:
    Strip(uint8_t* rgb_buf, uint16_t length)
        : _buf(rgb_buf), _len(length) {}

    void clear() { memset(_buf, 0, _len * 3); }

    void set_rgb(uint16_t idx, const rgb_t& c) {
        uint8_t* p = &_buf[idx * 3];
        p[0] = c.r;  // CRGB is RGB order; FastLED handles reorder to wire format
        p[1] = c.g;
        p[2] = c.b;
    }

    rgb_t get_rgb(uint16_t idx) const {
        const uint8_t* p = &_buf[idx * 3];
        return rgb_t(p[0], p[1], p[2]);
    }

    uint16_t length() const { return _len; }

private:
    uint8_t* _buf;
    uint16_t _len;
};
