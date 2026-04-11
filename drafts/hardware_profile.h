#pragma once

// Draft note for changes to:
//   src/hardware_profile.h
//
// This draft extends the output profile beyond strip length so caller-owned
// last-mile output code can choose the correct hardware channel order.

#include <cstdint>

// Draft note:
// Strip-related sizing is intentionally capped at 1000 LEDs. Pixel positions,
// strip lengths, and physical LED indices therefore use uint16_t.
static const uint16_t kMaxStripPixels = 1000;

enum class ColorOrder : uint8_t {
    RGB = 0,
    GRB = 1,
    BGR = 2,
    BRG = 3,
    GBR = 4,
    RBG = 5,
};

struct HardwareProfile {
    HardwareProfile()
        : strip_length(0), color_order(ColorOrder::RGB) {}

    explicit HardwareProfile(uint16_t length, ColorOrder order = ColorOrder::RGB)
        : strip_length(length), color_order(order) {}

    // Number of logical LEDs the playback output should cover.
    uint16_t strip_length;

    // Hardware-specific byte order expected by the final LED sink.
    //
    // Rendering still produces canonical RGB. This field is only for the
    // last-mile copy into a hardware-facing buffer.
    ColorOrder color_order;

    bool is_valid() const
    {
        return strip_length >= 1 && strip_length <= kMaxStripPixels;
    }

    bool operator==(const HardwareProfile& other) const
    {
        return strip_length == other.strip_length
            && color_order == other.color_order;
    }
};
