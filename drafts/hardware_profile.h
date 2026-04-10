#pragma once

// Draft note for changes to:
//   src/hardware_profile.h
//
// This draft extends the output profile beyond strip length so caller-owned
// last-mile output code can choose the correct hardware channel order.

#include <cstdint>

// Draft note:
// Strip-related sizing is intentionally capped at 250 LEDs, which allows
// strip-length and physical-pixel indexing to stay uint8_t in the redesign.
//
// This does not imply every runtime table index can be uint8_t. Counts such as
// total PixelViews, buffers, or events may still legitimately exceed 255 and
// therefore remain wider where needed.
static const uint8_t kMaxStripPixels = 250;

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

    explicit HardwareProfile(uint8_t length, ColorOrder order = ColorOrder::RGB)
        : strip_length(length), color_order(order) {}

    // Number of logical LEDs the playback output should cover.
    uint8_t strip_length;

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
