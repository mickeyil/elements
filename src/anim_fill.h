#pragma once

#include "animation.h"
#include "decoder.h"

class AnimFill : public Animation {
public:
    AnimFill(const FillParams& p) : _p(p) {}

    void render(hsva_t* buffer, uint8_t length, float t) override
    {
        (void)t;
        for (uint8_t i = 0; i < length; i++)
            buffer[i] = hsva_t(_p.color_h, _p.color_s, _p.color_v, 1.0f);
    }

private:
    FillParams _p;
};
