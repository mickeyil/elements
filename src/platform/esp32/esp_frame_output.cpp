#include "platform/esp32/esp_frame_output.h"

#include "core/strip.h"

// copy_to() writes packed r,g,b triples straight into the CRGB array.
static_assert(sizeof(CRGB) == 3, "CRGB must be a packed 3-byte pixel");

void EspFrameOutput::apply_profile(const HardwareProfile& profile)
{
    _gamma.set_gamma(profile.gamma);
    _order = profile.color_order;
}

void EspFrameOutput::write(const Strip& strip, float)
{
    // A physical strip longer than the profile shows black past the
    // program's pixels: copy_to() zeroes that tail.
    strip.copy_to(reinterpret_cast<uint8_t*>(_leds), MAX_STRIP_PIXELS,
                  _order, _gamma);
    FastLED.show();
}
