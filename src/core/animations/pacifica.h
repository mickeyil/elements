#pragma once

#include "core/animation.h"
#include "core/blob_reader.h"
#include "core/colors.h"
#include "core/fx_math.h"

#include <cstddef>
#include <cstdint>

// Pacifica animation: the FastLED blue-green ocean effect (Kriegsman and
// Corey March), four palette-mapped wave layers summed in 8-bit RGB, then
// whitecapped and deepened.
//
// The original keeps per-frame phase accumulators; this port computes those
// phases in closed form, so render() is a pure function of t_animation and
// synced devices produce identical frames. Compositing runs in an internal
// RGB scratch buffer that the decoder sizes via allocate_scratch(); the
// finished frame converts to HSV on the way into dst.

struct PacificaParams {
    float speed;       // time multiplier; 1 = the reference look
    float brightness;  // scales final V; 0..1, 0 = off
    float hue_shift;   // degrees added to final H
};

class Pacifica : public Animation {
public:
    explicit Pacifica(const PacificaParams& p);
    ~Pacifica() override;

    static Animation* from_blob(const uint8_t* params, size_t params_size,
                                DecodeError* err_out);

    // Size the RGB scratch to the dst view; the decoder calls this after
    // from_blob. render() is a no-op until it succeeds.
    bool allocate_scratch(uint16_t size);

    void render(PixelView& dst, float t_animation) override;

private:
    void render_wave_layer(const fx::palette16& pal, uint16_t n,
                           uint16_t cistart, uint16_t wavescale, uint8_t bri,
                           uint16_t ioff);
    void add_whitecaps(uint16_t n, uint32_t ms);
    void deepen_colors(uint16_t n);

    PacificaParams _p;
    rgb_t* _scratch = nullptr;
    uint16_t _scratch_size = 0;
};
