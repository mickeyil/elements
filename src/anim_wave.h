#pragma once

#include "animation.h"
#include <cmath>

// Which HSVA channel the wave acts on
enum class WaveChannel : uint8_t { H, S, V };

// Sine wave on a single HSV channel. The other channels are fixed.
//
// For pixel i at time t:
//   phase = 2π·t/period + phase0 + i·pixel_step
//   channel_value = min + (max - min) · (sin(phase)·0.5 + 0.5)

class AnimWave : public Animation {
public:
    AnimWave(WaveChannel channel,
             float fixed_h, float fixed_s, float fixed_v,
             float min, float max,
             float period, float phase0, float pixel_step)
        : _channel(channel),
          _fixed_h(fixed_h), _fixed_s(fixed_s), _fixed_v(fixed_v),
          _val_min(min), _range(max - min),
          _period(period), _phase0(phase0), _pixel_step(pixel_step) {}

    void render(hsva_t* buffer, uint8_t length, float t) override
    {
        float base_phase = (2.0f * M_PI * t / _period) + _phase0;

        for (uint8_t i = 0; i < length; i++) {
            float phase = base_phase + i * _pixel_step;
            float val = _val_min + _range * (sinf(phase) * 0.5f + 0.5f);

            float h = _fixed_h, s = _fixed_s, v = _fixed_v;
            switch (_channel) {
                case WaveChannel::H: h = val; break;
                case WaveChannel::S: s = val; break;
                case WaveChannel::V: v = val; break;
            }
            buffer[i] = hsva_t(h, s, v, 1.0f);
        }
    }

private:
    WaveChannel _channel;
    float _fixed_h, _fixed_s, _fixed_v;
    float _val_min, _range;
    float _period, _phase0, _pixel_step;
};
