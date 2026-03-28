#pragma once

#include <cstdint>

// Shared playback/device profile facts. Keep this header neutral so both the
// desktop/sim runtime and firmware runtime can depend on it.
static const uint16_t kMaxStripPixels = 250;

struct HardwareProfile {
    HardwareProfile()
        : strip_length(0) {}

    explicit HardwareProfile(uint16_t length)
        : strip_length(length) {}

    uint16_t strip_length;

    bool is_valid() const
    {
        return strip_length >= 1 && strip_length <= kMaxStripPixels;
    }

    bool operator==(const HardwareProfile& other) const
    {
        return strip_length == other.strip_length;
    }
};
