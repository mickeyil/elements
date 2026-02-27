#pragma once

#include "animation.h"
#include <cmath>

// Sine wave on brightness (V channel). H and S are fixed.
// V oscillates between v_min and v_max with configurable period,
// initial phase, and per-pixel phase offset.
//
// For pixel i at time t:
//   phase = 2π·t/period + phase0 + i·pixel_step
//   V = v_min + (v_max - v_min) · (sin(phase)·0.5 + 0.5)

class AnimSineWave : public Animation {
public:
    AnimSineWave(float hue, float sat, float v_min, float v_max,
                 float period, float phase0, float pixel_step)
        : _hue(hue), _sat(sat), _v_min(v_min), _v_max(v_max),
          _period(period), _phase0(phase0), _pixel_step(pixel_step) {}

    void render(hsva_t* buffer, uint8_t length, float t) override
    {
        float base_phase = (2.0f * M_PI * t / _period) + _phase0;
        float v_range = _v_max - _v_min;

        for (uint8_t i = 0; i < length; i++) {
            float phase = base_phase + i * _pixel_step;
            float v = _v_min + v_range * (sinf(phase) * 0.5f + 0.5f);
            buffer[i] = hsva_t(_hue, _sat, v, 1.0f);
        }
    }

private:
    float _hue;
    float _sat;
    float _v_min;
    float _v_max;
    float _period;
    float _phase0;
    float _pixel_step;
};
