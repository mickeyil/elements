#pragma once

#include "animation.h"
#include "decoder.h"
#include <cmath>
#include <cstring>

// Shift animation: slides a snapshot of pixels over time.
// The work buffer is pre-filled by the engine with the correct source pixels.

class AnimShift : public Animation {
public:
    AnimShift(const ShiftParams& p, hsva_t* work_buf, uint8_t work_len)
        : _p(p), _work(work_buf), _work_len(work_len) {}

    void render(hsva_t* buffer, uint8_t length, float t) override
    {
        float offset = _p.velocity * t;
        // direction: 0=left, 1=right
        if (_p.direction == 0)
            offset = -offset;

        hsva_t fill(_p.fill_h, _p.fill_s, _p.fill_v, _p.fill_a);

        for (uint8_t i = 0; i < length; i++) {
            float src_f = i - offset;
            int src_i = (int)floorf(src_f);

            if (_p.circular) {
                // Wrap around
                src_i = ((src_i % _work_len) + _work_len) % _work_len;
                buffer[i] = _work[src_i];
            } else {
                if (src_i >= 0 && src_i < _work_len) {
                    buffer[i] = _work[src_i];
                } else {
                    buffer[i] = fill;
                }
            }
        }
    }

private:
    ShiftParams _p;
    hsva_t* _work;
    uint8_t _work_len;
};
