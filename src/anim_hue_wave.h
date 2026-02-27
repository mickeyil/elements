#pragma once

#include "animation.h"
#include <cmath>

// Slow hue oscillation around a center hue. Each pixel gets a slight phase
// offset for visual interest. Saturation and value are constant.

class AnimHueWave : public Animation {
public:
    // center_hue: base hue in degrees (e.g., 220 for deep blue)
    // hue_range: oscillation amplitude in degrees (±hue_range around center)
    // period: full cycle time in seconds
    // phase_spread: phase offset between first and last pixel (in radians)
    // sat, val: constant saturation and value (0-1)
    AnimHueWave(float center_hue, float hue_range, float period,
                float phase_spread, float sat, float val)
        : _center_hue(center_hue), _hue_range(hue_range), _period(period),
          _phase_spread(phase_spread), _sat(sat), _val(val) {}

    void render(hsva_t* buffer, uint8_t length, float t) override
    {
        float base_phase = t / _period * 2.0f * M_PI;

        for (uint8_t i = 0; i < length; i++) {
            float pixel_phase = (length > 1)
                ? _phase_spread * (float)i / (float)(length - 1)
                : 0.0f;

            float hue = _center_hue + _hue_range * sinf(base_phase + pixel_phase);
            buffer[i] = hsva_t(hue, _sat, _val, 1.0f);
        }
    }

private:
    float _center_hue;
    float _hue_range;
    float _period;
    float _phase_spread;
    float _sat;
    float _val;
};
