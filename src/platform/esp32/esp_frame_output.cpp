#include "platform/esp32/esp_frame_output.h"

#include <cstring>

#include "core/strip.h"

void EspFrameOutput::apply_profile(const HardwareProfile& profile)
{
    _gamma.set_gamma(profile.gamma);
    _order = profile.color_order;
}

void EspFrameOutput::write(const Strip& strip, float)
{
    const uint16_t n = strip.size();
    for (uint16_t i = 0; i < n; ++i) {
        const rgb_t c = _gamma.correct(strip[i]);
        _leds[i] = (_order == ColorOrder::BGR) ? CRGB(c.b, c.g, c.r)
                                               : CRGB(c.r, c.g, c.b);
    }
    // A physical strip longer than the profile shows black past the
    // program's pixels.
    if (n < MAX_STRIP_PIXELS) {
        std::memset(_leds + n, 0, (MAX_STRIP_PIXELS - n) * sizeof(CRGB));
    }
    FastLED.show();
}
