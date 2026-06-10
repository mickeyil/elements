#include <Arduino.h>
#include <FastLED.h>

namespace {

constexpr uint8_t LED_PIN = 13;
constexpr uint16_t NUM_LEDS = 50;

CRGB g_leds[NUM_LEDS];

}  // namespace

void setup()
{
    FastLED.addLeds<WS2812B, LED_PIN, GRB>(g_leds, NUM_LEDS);
    FastLED.setBrightness(255);

    fill_solid(g_leds, NUM_LEDS, CRGB::Black);
    g_leds[0] = CRGB::White;
    FastLED.show();
}

void loop()
{
    delay(1000);
}
