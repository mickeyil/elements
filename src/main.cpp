#include <Arduino.h>
#include <FastLED.h>

#include "strip.h"
#include "layer.h"
#include "compositor.h"
#include "anim_wave.h"
#include "anim_spark.h"

// --- Hardware config ---
#define LED_PIN     13
#define NUM_LEDS    2

// --- Render config ---
#define FRAME_PERIOD_MS  20   // 50Hz refresh rate

// FastLED's output buffer
CRGB crgb[NUM_LEDS];

// --- Rendering pipeline ---
Strip strip((uint8_t*)crgb, NUM_LEDS);

// Layer 0: background sine wave on all LEDs
static const uint8_t bg_indices[] = {0, 1};
Layer bg_layer(0, bg_indices, NUM_LEDS, /*priority=*/0);

// Layer 1: spark overlay on all LEDs
static const uint8_t spark_indices[] = {0, 1};
Layer spark_layer(1, spark_indices, NUM_LEDS, /*priority=*/1);

// Animations
AnimWave bg_wave(
    WaveChannel::V,   // animate brightness
    220.0f, 1.0f, 0,  // fixed H=220 (deep blue), S=1.0, V ignored (animated)
    0.0f, 0.4f,       // V range: 0.0 – 0.4
    8.0f,             // period: 8 second cycle
    -M_PI / 2,        // phase0: pixel 0 starts at v_min
    M_PI              // pixel_step: π — LEDs opposite phase
);

AnimSpark spark(
    4.0f,         // interval: every 4 seconds
    0.25f         // fade time: 250ms quick flash
);

Compositor compositor(strip);

void setup()
{
    FastLED.addLeds<WS2811, LED_PIN, GRB>(crgb, NUM_LEDS);
    FastLED.setBrightness(255);

    bg_layer.set_animation(&bg_wave);
    spark_layer.set_animation(&spark);

    compositor.add_layer(&bg_layer);
    compositor.add_layer(&spark_layer);
}

void loop()
{
    float t = millis() / 1000.0f;
    compositor.render(t);
    FastLED.show();
    delay(FRAME_PERIOD_MS);
}
