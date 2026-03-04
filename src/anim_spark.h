#pragma once

#include "animation.h"
#include "decoder.h"
#include <cmath>

// Single flash at t=0, fades alpha to 0 over _fade seconds.
// No repeating — the DSL schedules multiple spark events for repetition.

class AnimSpark : public Animation {
public:
    AnimSpark(const SparkParams& p)
        : _color_h(p.color_h), _color_s(p.color_s), _color_v(p.color_v),
          _fade(p.fade) {}

    void render(hsva_t* buffer, uint8_t length, float t) override
    {
        float alpha;
        if (t < _fade) {
            alpha = 1.0f - (t / _fade);
            alpha = alpha * alpha;  // quadratic ease-out
        } else {
            alpha = 0.0f;
        }

        for (uint8_t i = 0; i < length; i++) {
            buffer[i] = hsva_t(_color_h, _color_s, _color_v, alpha);
        }
    }

private:
    float _color_h, _color_s, _color_v;
    float _fade;
};
