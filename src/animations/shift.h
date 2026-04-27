#pragma once

#include "animation.h"
#include "blob_reader.h"

#include <cstddef>
#include <cstdint>

// Shift animation: initialize() snapshots the source view into the work
// view; render() draws a shifted copy of that snapshot.
//
// `direction` picks left vs right; `velocity` is in pixels per second.
// Circular wraps modulo work size; non-circular fills exposed pixels with
// `fill`.

struct ShiftParams {
    uint8_t direction;       // 0=left, 1=right
    float velocity;           // pixels per second
    uint8_t circular;         // 0=non-circular, 1=circular
    float fill_h, fill_s, fill_v, fill_a;
};

class Shift : public Animation {
public:
    explicit Shift(const ShiftParams& p);

    static Animation* from_blob(const uint8_t* params, size_t params_size,
                                DecodeError* err_out);

    // Snapshots `src` into `work`. `work` is also remembered for render().
    void initialize(const PixelView* src, PixelView* work) override;

    void render(PixelView& dst, float t_animation) override;

private:
    ShiftParams _p;
    PixelView* _work = nullptr;
};
