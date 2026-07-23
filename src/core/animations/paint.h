#pragma once

#include "core/animation.h"
#include "core/blob_reader.h"
#include "core/colors.h"

#include <cstddef>
#include <cstdint>

// Paint animation: writes a fixed pattern to dst on every frame.
//
// Two shapes:
//   - solid: every dst pixel takes the same color
//   - constant: a constant hsva array is baked into the blob and replayed
//     into dst as dst[i] = constant[i]. The constant length MUST equal
//     dst.size(); the compiler enforces this and the decoder rejects any
//     mismatch, so render() does not re-check.
//
// Blob params: u8 mode. Mode 0 (solid): 4 float32 (h, s, v, a). Mode 1
// (constant): u16 count, then count * (4 float32: h, s, v, a). count is a
// u16 so a per-pixel paint can cover a full MAX_STRIP_PIXELS strip.

class Paint : public Animation {
public:
    // Solid color across every dst pixel.
    Paint(float h, float s, float v, float a);

    // Constant-array mode. Takes ownership of `constant`.
    Paint(hsva_t* constant, uint16_t count);

    ~Paint() override;

    static Animation* from_blob(const uint8_t* params, size_t params_size,
                                DecodeError* err_out);

    void render(PixelView& dst, float t_animation) override;

    // Length of the constant array, or 0 in solid mode. The decoder calls
    // this to verify the array matches the dst view size before accepting
    // the event.
    uint16_t constant_array_size() const {
        return _mode == Mode::Constant ? _constant_count : 0;
    }

private:
    enum class Mode : uint8_t { Solid = 0, Constant = 1 };

    Mode _mode;
    hsva_t _solid;
    hsva_t* _constant;
    uint16_t _constant_count;
};
