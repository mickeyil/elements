#include <Arduino.h>
#include <FastLED.h>

#include "strip.h"
#include "engine.h"
#include "decoder.h"

// --- Hardware config ---
#define LED_PIN     13
#define NUM_LEDS    2

// --- Render config ---
#define FRAME_PERIOD_MS  20   // 50Hz refresh rate

// FastLED's output buffer
CRGB crgb[NUM_LEDS];

// --- Rendering pipeline ---
Strip strip((uint8_t*)crgb, NUM_LEDS);
Engine* engine = nullptr;

void setup()
{
    FastLED.addLeds<WS2811, LED_PIN, GRB>(crgb, NUM_LEDS);
    FastLED.setBrightness(255);

    // TODO: receive blob via MQTT. For now, no embedded blob.
    // Program* prog = decode_program(blob_data, blob_len);
    // if (prog) engine = new Engine(prog, strip);
}

void loop()
{
    float t = millis() / 1000.0f;
    if (engine) {
        if (!engine->tick(t)) {
            // Program ended — leave last frame on strip
        }
    }
    FastLED.show();
    delay(FRAME_PERIOD_MS);
}
