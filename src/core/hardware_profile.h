#pragma once

#include <cstdint>

#include "core/gamma.h"

// Describes the physical LED strip a runtime is driving: how many pixels
// it has, the channel order it uses, and the output gamma to apply before
// emitting pixel values.
//
// Constructed once at startup (or on profile change) from device
// configuration and passed to the renderer / output path. Default-
// constructed instances are intentionally invalid (strip_length == 0);
// is_valid() distinguishes "configured" from "uninitialized".

// Upper bound on supported strip lengths. Profiles outside
// [1, MAX_STRIP_PIXELS] fail is_valid(). Sized to the realistic
// hardware ceiling; it also bounds the firmware LED buffer and keeps
// a full sim preview frame inside one UDP packet.
static constexpr uint16_t MAX_STRIP_PIXELS = 300;

// Order of color channels written to the output.
enum class ColorOrder : uint8_t {
    RGB = 0,
    BGR = 1,
};

struct HardwareProfile {
    HardwareProfile()
        : strip_length(0),
          color_order(ColorOrder::RGB),
          gamma(IDENTITY_GAMMA) {}

    explicit HardwareProfile(uint16_t length,
                             ColorOrder order = ColorOrder::RGB,
                             float output_gamma = IDENTITY_GAMMA)
        : strip_length(length),
          color_order(order),
          gamma(output_gamma) {}

    // Number of pixels on the strip. Zero indicates an uninitialized profile.
    uint16_t strip_length;

    ColorOrder color_order;

    // Gamma applied before output. IDENTITY_GAMMA (1.0) means no correction;
    // see gamma.h for the supported range.
    float gamma;

    bool is_valid() const
    {
        return strip_length >= 1 && strip_length <= MAX_STRIP_PIXELS;
    }

    bool operator==(const HardwareProfile& other) const
    {
        return strip_length == other.strip_length
            && color_order == other.color_order
            && gamma == other.gamma;
    }
};
