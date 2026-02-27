#include <Arduino.h>
#include <FastLED.h>

#include "strip.h"
#include "layer.h"
#include "compositor.h"
#include "anim_hue_wave.h"
#include "anim_spark.h"

// --- Hardware config ---
#define LED_PIN     13
#define NUM_LEDS    2

// FastLED's output buffer
CRGB crgb[NUM_LEDS];

// --- Rendering pipeline ---
Strip strip((uint8_t*)crgb, NUM_LEDS);

// Layer 0: background hue wave on all LEDs
static const uint8_t bg_indices[] = {0, 1};
Layer bg_layer(0, bg_indices, NUM_LEDS, /*priority=*/0);

// Layer 1: spark overlay on all LEDs
static const uint8_t spark_indices[] = {0, 1};
Layer spark_layer(1, spark_indices, NUM_LEDS, /*priority=*/1);

// Animations
AnimHueWave hue_wave(
    220.0f,   // center hue: deep blue
    20.0f,    // hue range: ±20° oscillation
    8.0f,     // period: 8 second full cycle
    1.0f,     // phase spread: 1 radian between first and last pixel
    1.0f,     // saturation: full
    0.35f     // value: moderate (not too bright, chill)
);

AnimSpark spark(
    4.0f,     // interval: every 4 seconds
    1.0f      // fade time: 1 second decay
);

Compositor compositor(strip);

void setup()
{
    FastLED.addLeds<WS2811, LED_PIN, GRB>(crgb, NUM_LEDS);
    FastLED.setBrightness(255);

    bg_layer.set_animation(&hue_wave);
    spark_layer.set_animation(&spark);

    compositor.add_layer(&bg_layer);
    compositor.add_layer(&spark_layer);
}

void loop()
{
    float t = millis() / 1000.0f;
    compositor.render(t);
    FastLED.show();
    delay(10);  // ~100Hz
}
