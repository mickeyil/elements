#pragma once

#include "animation.h"
#include "blob_reader.h"

#include <cstddef>

// Spark animation: a single flash with a quadratic alpha fade-out. At t=0
// the color is full alpha; at t=fade alpha hits zero and stays there.

struct SparkParams {
    float color_h, color_s, color_v;
    float fade;     // seconds; must be > 0
};

class Spark : public Animation {
public:
    explicit Spark(const SparkParams& p);

    static Animation* from_blob(const uint8_t* params, size_t params_size,
                                DecodeError* err_out);

    void render(PixelView& dst, float t_animation) override;

private:
    SparkParams _p;
};
