#pragma once

#include <FastLED.h>

#include "platform/frame_output.h"
#include "core/gamma.h"
#include "core/hardware_profile.h"

// FrameOutput for the firmware build: turns the program-space strip
// into LED output. apply_profile() builds the gamma LUT and latches
// the channel order; write() corrects each pixel into the FastLED
// buffer, zero-pads the tail, and latches with FastLED.show(). The
// sim counterpart is SimFrameOutput.
//
// The CRGB buffer lives here, sized to the system maximum. The entry
// point registers it with FastLED.addLeds() at boot; the LED pin is a
// compile-time template argument, so registration cannot move into
// this class.

class EspFrameOutput : public FrameOutput
{
public:
    void apply_profile(const HardwareProfile& profile) override;
    void write(const Strip& strip, float t_program) override;

    // FastLED registration target, MAX_STRIP_PIXELS long.
    CRGB* leds() { return _leds; }

private:
    CRGB _leds[MAX_STRIP_PIXELS] = {};
    GammaCorrection _gamma;          // identity until a profile arrives
    ColorOrder _order = ColorOrder::RGB;
};
