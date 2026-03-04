#pragma once

#include "animation.h"
#include "decoder.h"
#include <cmath>

class AnimWave : public Animation {
public:
    AnimWave(const WaveParams& p)
        : _channel(p.channel),
          _fixed_h(p.h), _fixed_s(p.s), _fixed_v(p.v),
          _val_min(p.min_val), _range(p.max_val - p.min_val),
          _period(p.period), _phase0(p.phase0), _pixel_step(p.pixel_step) {}

    void render(hsva_t* buffer, uint8_t length, float t) override
    {
        float base_phase = (2.0f * M_PI * t / _period) + _phase0;

        for (uint8_t i = 0; i < length; i++) {
            float phase = base_phase + i * _pixel_step;
            float val = _val_min + _range * (sinf(phase) * 0.5f + 0.5f);

            float h = _fixed_h, s = _fixed_s, v = _fixed_v;
            switch (_channel) {
                case 0: h = val; break;  // H
                case 1: s = val; break;  // S
                case 2: v = val; break;  // V
            }
            buffer[i] = hsva_t(h, s, v, 1.0f);
        }
    }

private:
    uint8_t _channel;
    float _fixed_h, _fixed_s, _fixed_v;
    float _val_min, _range;
    float _period, _phase0, _pixel_step;
};
