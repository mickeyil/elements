#pragma once

#include "animation.h"
#include "blob_reader.h"

#include <cstddef>
#include <cstdint>

// Wave animation: modulates one HSV channel sinusoidally over time. The
// modulated channel takes value sin(2*pi*t/period + phase0 + i*pixel_step),
// rescaled into [min_val, max_val]. The other two channels stay fixed.

struct WaveParams {
    uint8_t channel;          // 0=H, 1=S, 2=V
    float h, s, v;             // values for the unmodulated channels
    float min_val, max_val;    // output range for the modulated channel
    float period;
    float phase0;
    float pixel_step;          // phase added per pixel
};

class Wave : public Animation {
public:
    explicit Wave(const WaveParams& p);

    // Parse blob params. Returns nullptr and sets *err_out on failure.
    static Animation* from_blob(const uint8_t* params, size_t params_size,
                                DecodeError* err_out);

    void render(PixelView& dst, float t_animation) override;

private:
    WaveParams _p;
};
