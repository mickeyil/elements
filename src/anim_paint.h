#pragma once

#include "animation.h"
#include "decoder.h"
#include <cstring>

class AnimPaint : public Animation {
public:
    AnimPaint(const PaintParams& p) : _p(p) {}

    void render(hsva_t* buffer, uint8_t length, float t) override
    {
        (void)t;
        if (_p.mode == 0) {
            for (uint8_t i = 0; i < length; i++)
                buffer[i] = hsva_t(_p.color_h, _p.color_s, _p.color_v, _p.color_a);
        } else {
            uint8_t n = length < _p.pixel_count ? length : _p.pixel_count;
            memcpy(buffer, _p.pixels, n * sizeof(hsva_t));
        }
    }

private:
    PaintParams _p;
};
