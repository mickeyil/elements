#pragma once

#include <cstddef>
#include <cstdint>
#include <cstring>
#include "colors.h"

// Thin wrapper around the physical LED output buffer (FastLED's CRGB array).
// Provides indexed RGB access. The compositor blends directly into this.

class Strip {
public:
    Strip(uint8_t* rgb_buf = nullptr, uint16_t length = 0)
        : _buf(rgb_buf), _len(length) {}

    void rebind(uint8_t* rgb_buf, uint16_t length)
    {
        _buf = rgb_buf;
        _len = length;
    }

    void clear()
    {
        if (!_buf || _len == 0) {
            return;
        }
        memset(_buf, 0, byte_size());
    }

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
    size_t byte_size() const { return static_cast<size_t>(_len) * 3; }
    uint8_t* data() { return _buf; }
    const uint8_t* data() const { return _buf; }

private:
    uint8_t* _buf;
    uint16_t _len;
};
