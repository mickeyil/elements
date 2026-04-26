#pragma once

#include <cstdint>

#include "gamma.h"

// Shared playback/device profile facts. Keep this header neutral so both the
// desktop/sim runtime and firmware runtime can depend on it.
static constexpr uint16_t MAX_STRIP_PIXELS = 1000;

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

    uint16_t strip_length;
    ColorOrder color_order;
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
