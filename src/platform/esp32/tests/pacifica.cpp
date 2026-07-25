// Standalone strip test: Pacifica, the FastLED blue-green ocean wave
// animation, looping forever.
//
// Ported from FastLED examples/Pacifica/Pacifica.ino
// (Mark Kriegsman and Mary Corey March, December 2019).

#include <Arduino.h>
#include <FastLED.h>

namespace {

constexpr uint8_t LED_PIN = 13;
constexpr uint16_t NUM_LEDS = 50;

CRGB g_leds[NUM_LEDS];

// Blue-green palettes from the waters off southern California.
const CRGBPalette16 pacifica_palette_1 =
    { 0x000507, 0x000409, 0x00030B, 0x00030D, 0x000210, 0x000212, 0x000114, 0x000117,
      0x000019, 0x00001C, 0x000026, 0x000031, 0x00003B, 0x000046, 0x14554B, 0x28AA50 };
const CRGBPalette16 pacifica_palette_2 =
    { 0x000507, 0x000409, 0x00030B, 0x00030D, 0x000210, 0x000212, 0x000114, 0x000117,
      0x000019, 0x00001C, 0x000026, 0x000031, 0x00003B, 0x000046, 0x0C5F52, 0x19BE5F };
const CRGBPalette16 pacifica_palette_3 =
    { 0x000208, 0x00030E, 0x000514, 0x00061A, 0x000820, 0x000927, 0x000B2D, 0x000C33,
      0x000E39, 0x001040, 0x001450, 0x001860, 0x001C70, 0x002080, 0x1040BF, 0x2060FF };

// Add one layer of waves into the led array.
void pacifica_one_layer(const CRGBPalette16& p, uint16_t cistart, uint16_t wavescale,
                        uint8_t bri, uint16_t ioff)
{
    uint16_t ci = cistart;
    uint16_t waveangle = ioff;
    uint16_t wavescale_half = (wavescale / 2) + 20;
    for (uint16_t i = 0; i < NUM_LEDS; i++) {
        waveangle += 250;
        uint16_t s16 = sin16(waveangle) + 32768;
        uint16_t cs = scale16(s16, wavescale_half) + wavescale_half;
        ci += cs;
        uint16_t sindex16 = sin16(ci) + 32768;
        uint8_t sindex8 = scale16(sindex16, 240);
        g_leds[i] += ColorFromPalette(p, sindex8, bri, LINEARBLEND);
    }
}

// Add extra 'white' where the four wave layers line up brightly.
void pacifica_add_whitecaps()
{
    uint8_t basethreshold = beatsin8(9, 55, 65);
    uint8_t wave = beat8(7);
    for (uint16_t i = 0; i < NUM_LEDS; i++) {
        uint8_t threshold = scale8(sin8(wave), 20) + basethreshold;
        wave += 7;
        uint8_t l = g_leds[i].getAverageLight();
        if (l > threshold) {
            uint8_t overage = l - threshold;
            uint8_t overage2 = qadd8(overage, overage);
            g_leds[i] += CRGB(overage, overage2, qadd8(overage2, overage2));
        }
    }
}

// Deepen the blues and greens.
void pacifica_deepen_colors()
{
    for (uint16_t i = 0; i < NUM_LEDS; i++) {
        g_leds[i].blue = scale8(g_leds[i].blue, 145);
        g_leds[i].green = scale8(g_leds[i].green, 200);
        g_leds[i] |= CRGB(2, 5, 7);
    }
}

// Four wave layers with independently drifting speeds and scales,
// summed, then whitecapped and deepened.
void pacifica_frame()
{
    static uint16_t sCIStart1, sCIStart2, sCIStart3, sCIStart4;
    static uint32_t sLastms = 0;
    uint32_t ms = millis();
    uint32_t deltams = ms - sLastms;
    sLastms = ms;
    uint16_t speedfactor1 = beatsin16(3, 179, 269);
    uint16_t speedfactor2 = beatsin16(4, 179, 269);
    uint32_t deltams1 = (deltams * speedfactor1) / 256;
    uint32_t deltams2 = (deltams * speedfactor2) / 256;
    uint32_t deltams21 = (deltams1 + deltams2) / 2;
    sCIStart1 += (deltams1 * beatsin88(1011, 10, 13));
    sCIStart2 -= (deltams21 * beatsin88(777, 8, 11));
    sCIStart3 -= (deltams1 * beatsin88(501, 5, 7));
    sCIStart4 -= (deltams2 * beatsin88(257, 4, 6));

    fill_solid(g_leds, NUM_LEDS, CRGB(2, 6, 10));

    pacifica_one_layer(pacifica_palette_1, sCIStart1, beatsin16(3, 11 * 256, 14 * 256), beatsin8(10, 70, 130), 0 - beat16(301));
    pacifica_one_layer(pacifica_palette_2, sCIStart2, beatsin16(4, 6 * 256, 9 * 256), beatsin8(17, 40, 80), beat16(401));
    pacifica_one_layer(pacifica_palette_3, sCIStart3, 6 * 256, beatsin8(9, 10, 38), 0 - beat16(503));
    pacifica_one_layer(pacifica_palette_3, sCIStart4, 5 * 256, beatsin8(8, 10, 28), beat16(601));

    pacifica_add_whitecaps();
    pacifica_deepen_colors();
}

}  // namespace

void setup()
{
    FastLED.addLeds<WS2812B, LED_PIN, RGB>(g_leds, NUM_LEDS)
        .setCorrection(TypicalLEDStrip);
    FastLED.setBrightness(255);
    // Headroom under the rig's 2 A supply.
    FastLED.setMaxPowerInVoltsAndMilliamps(5, 1800);
}

void loop()
{
    pacifica_frame();
    FastLED.show();
    delay(20);
}
