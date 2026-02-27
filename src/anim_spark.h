#pragma once

#include "animation.h"
#include <cmath>

// Periodic white spark: flashes all pixels in the layer to full white,
// then fades out via alpha decay. The cycle repeats every `interval` seconds.

class AnimSpark : public Animation {
public:
    // interval: seconds between sparks
    // fade_time: seconds for the spark to fade from full to transparent
    AnimSpark(float interval, float fade_time)
        : _interval(interval), _fade_time(fade_time) {}

    void render(hsva_t* buffer, uint8_t length, float t) override
    {
        // Time within current spark cycle
        float cycle_t = fmod(t, _interval);

        float alpha;
        if (cycle_t < _fade_time) {
            // Spark is active: fade from 1.0 to 0.0
            alpha = 1.0f - (cycle_t / _fade_time);
            // Ease out (quadratic) for a more natural fade
            alpha = alpha * alpha;
        } else {
            // Spark is inactive: fully transparent
            alpha = 0.0f;
        }

        for (uint8_t i = 0; i < length; i++) {
            // White: H=0, S=0, V=1.0
            buffer[i] = hsva_t(0, 0, 1.0f, alpha);
        }
    }

private:
    float _interval;
    float _fade_time;
};
