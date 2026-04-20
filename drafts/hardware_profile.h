#pragma once

// Draft note for changes to:
//   src/hardware_profile.h
//
// This draft extends the output profile beyond strip length so caller-owned
// last-mile output code can choose the correct hardware channel order.

#include <cstdint>

#include "gamma.h"

// Draft note:
// Strip-related sizing is intentionally capped at 1000 LEDs. Pixel positions,
// strip lengths, and physical LED indices therefore use uint16_t.
static const uint16_t kMaxStripPixels = 1000;

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

    // Number of logical LEDs the playback output should cover.
    uint16_t strip_length;

    // Hardware-specific byte order expected by the final LED sink.
    //
    // Rendering still produces canonical RGB. This field is only for the
    // last-mile copy into a hardware-facing buffer. The current draft only
    // supports the two observed hardware layouts; more can be added later if a
    // real strip requires them.
    ColorOrder color_order;

    // Desired last-mile output gamma. Playback stores this profile for owner
    // convenience, but Engine and Compositor do not consume it.
    //
    // The owner applies this to its GammaCorrection object. Invalid values are
    // ignored by GammaCorrection::set_gamma() rather than making the strip
    // profile unusable.
    float gamma;

    bool is_valid() const
    {
        return strip_length >= 1 && strip_length <= kMaxStripPixels;
    }

    bool operator==(const HardwareProfile& other) const
    {
        return strip_length == other.strip_length
            && color_order == other.color_order
            && gamma == other.gamma;
    }
};
