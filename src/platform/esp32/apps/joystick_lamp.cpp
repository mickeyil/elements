// Standalone joystick lamp: 16 WS2812B pixels cycling through three core
// animations (Pacifica, red alert wave, solid soft color), controlled by a
// KY-023 stick. Vertical taps toggle power, vertical holds ramp intensity,
// horizontal taps switch animations, horizontal holds rotate hue. Gesture
// and lamp logic live in app/joystick_gestures for host testing; this file
// only reads the ADC and pushes frames.

#include <Arduino.h>
#include <FastLED.h>

#include "app/joystick_gestures.h"
#include "core/animations/pacifica.h"
#include "core/animations/paint.h"
#include "core/animations/wave.h"
#include "core/colors.h"
#include "core/pixel_view.h"

namespace {

constexpr uint8_t LED_PIN = 13;
constexpr uint16_t NUM_LEDS = 16;

// KY-023 on 3V3: both axes on ADC1 so WiFi can never steal the pins.
constexpr uint8_t PIN_VRX = 34;
constexpr uint8_t PIN_VRY = 35;
constexpr uint8_t PIN_SW = 25;  // wired but unused for now

// Which ADC direction is up/right is unverified; flip on hardware if wrong.
constexpr float X_SIGN = 1.0f;
constexpr float Y_SIGN = 1.0f;

constexpr uint32_t FRAME_MS = 20;  // ~50 fps
constexpr uint16_t ADC_MAX = 4095;

CRGB g_leds[NUM_LEDS];
hsva_t g_frame[NUM_LEDS];
PixelView g_view;

JoystickGestures g_gestures;
LampState g_lamp;

uint16_t g_center_x = ADC_MAX / 2;
uint16_t g_center_y = ADC_MAX / 2;

Pacifica g_pacifica(PacificaParams{1.0f, 1.0f, 0.0f});
// Mirrors animations/ring35_red_alert.py: V swings 0..1 once per second.
Wave g_red_alert(WaveParams{2, 0.0f, 1.0f, 0.0f, 0.0f, 1.0f, 1.0f,
                            float(HALF_PI), 0.0f});
// Mid saturation so the hue hold reads as a visible tint change.
Paint g_soft(0.0f, 0.6f, 1.0f, 1.0f);

Animation* g_anims[LampState::NUM_ANIMS] = {&g_pacifica, &g_red_alert, &g_soft};

const char* g_anim_names[LampState::NUM_ANIMS] = {"pacifica", "red_alert",
                                                  "soft"};

const char* axis_name(GestureAxis a)
{
    return a == GestureAxis::Horizontal ? "H" : "V";
}

// Median of 5 reads; kills the single-sample spikes these pots produce.
uint16_t read_adc_median(uint8_t pin)
{
    uint16_t s[5];
    for (int i = 0; i < 5; i++) s[i] = analogRead(pin);
    for (int i = 1; i < 5; i++) {
        uint16_t v = s[i];
        int j = i - 1;
        while (j >= 0 && s[j] > v) { s[j + 1] = s[j]; j--; }
        s[j + 1] = v;
    }
    return s[2];
}

// Map a raw reading to [-1, 1] around the boot-time center. The two sides
// scale independently because the pot rarely rests at exactly half scale.
float normalize(uint16_t raw, uint16_t center)
{
    const float span = raw >= center ? float(ADC_MAX - center) : float(center);
    if (span < 1.0f) return 0.0f;
    float v = (float(raw) - float(center)) / span;
    if (v < -1.0f) v = -1.0f;
    if (v > 1.0f) v = 1.0f;
    return v;
}

// Assume the stick is at rest during boot and call that the center.
void calibrate_center()
{
    uint32_t sum_x = 0, sum_y = 0;
    constexpr int SAMPLES = 16;
    for (int i = 0; i < SAMPLES; i++) {
        sum_x += read_adc_median(PIN_VRX);
        sum_y += read_adc_median(PIN_VRY);
        delay(5);
    }
    g_center_x = sum_x / SAMPLES;
    g_center_y = sum_y / SAMPLES;
}

}  // namespace

void setup()
{
    Serial.begin(115200);
    delay(300);  // let USB-serial settle so the banner is not lost
    Serial.println("\n[init] joystick_lamp");
    Serial.printf("[init] led=%u n=%u vrx=%u vry=%u sw=%u\n", LED_PIN, NUM_LEDS,
                  PIN_VRX, PIN_VRY, PIN_SW);

    FastLED.addLeds<WS2812B, LED_PIN, GRB>(g_leds, NUM_LEDS)
        .setCorrection(TypicalLEDStrip);
    FastLED.setBrightness(255);
    // Testing off a bus-powered USB3 hub: ~900 mA shared budget, minus the
    // ESP32 board itself. Restore 1800 for the rig's 2 A supply.
    FastLED.setMaxPowerInVoltsAndMilliamps(5, 500);

    pinMode(PIN_SW, INPUT_PULLUP);
    analogReadResolution(12);
    calibrate_center();
    Serial.printf("[init] center x=%u y=%u (stick must be at rest during boot)\n",
                  g_center_x, g_center_y);

    g_view.initialize(g_frame, NUM_LEDS);
    g_pacifica.allocate_scratch(NUM_LEDS);
}

void loop()
{
    static bool s_first = true;
    static uint32_t s_last_stick_log = 0;
    static bool s_was_hold = false;
    static uint32_t s_hold_ms = 0;
    static uint32_t s_last_hold_log = 0;

    const uint32_t now = millis();
    if (s_first) {
        s_first = false;
        Serial.println("[loop] start");
    }

    const uint16_t raw_x = read_adc_median(PIN_VRX);
    const uint16_t raw_y = read_adc_median(PIN_VRY);
    const float x = X_SIGN * normalize(raw_x, g_center_x);
    const float y = Y_SIGN * normalize(raw_y, g_center_y);

    // Trace deflections above half the engage threshold so wrong signs and
    // off-center calibration are visible without spamming at rest.
    if ((fabsf(x) > 0.10f || fabsf(y) > 0.10f) &&
        now - s_last_stick_log >= 200) {
        s_last_stick_log = now;
        Serial.printf("[stick] x=%+.2f y=%+.2f raw=%u/%u\n", x, y, raw_x,
                      raw_y);
    }

    const GestureResult g = g_gestures.update(x, y, now);
    const bool prev_on = g_lamp.on();
    const int prev_anim = g_lamp.anim_index();
    g_lamp.apply(g);

    if (g.short_press) {
        Serial.printf("[gesture] short %s%c\n", axis_name(g.axis),
                      g.sign > 0 ? '+' : '-');
    }
    if (g.hold) {
        if (!s_was_hold) {
            s_hold_ms = 0;
            s_last_hold_log = now;
            Serial.printf("[gesture] hold start %s%c\n", axis_name(g.axis),
                          g.sign > 0 ? '+' : '-');
        }
        s_hold_ms += g.hold_dt_ms;
        if (now - s_last_hold_log >= 300) {
            s_last_hold_log = now;
            Serial.printf("[hold] %lums intensity=%.2f hue=%.0f\n",
                          (unsigned long)s_hold_ms, g_lamp.intensity(),
                          g_lamp.hue_for_current());
        }
    } else if (s_was_hold) {
        Serial.printf("[gesture] hold end %lums intensity=%.2f hue=%.0f\n",
                      (unsigned long)s_hold_ms, g_lamp.intensity(),
                      g_lamp.hue_for_current());
    }
    s_was_hold = g.hold;

    if (g_lamp.on() != prev_on) {
        Serial.printf("[lamp] power %s\n", g_lamp.on() ? "ON" : "OFF");
    }
    if (g_lamp.anim_index() != prev_anim) {
        Serial.printf("[lamp] anim -> %d (%s)\n", g_lamp.anim_index(),
                      g_anim_names[g_lamp.anim_index()]);
    }

    if (g_lamp.on()) {
        g_anims[g_lamp.anim_index()]->render(g_view, now / 1000.0f);
        const float hue = g_lamp.hue_for_current();
        const float intensity = g_lamp.intensity();
        for (uint16_t i = 0; i < NUM_LEDS; i++) {
            const hsva_t& p = g_frame[i];
            const rgb_t c =
                hsv_to_rgb(wrap360(p.h + hue), p.s, p.v * intensity);
            g_leds[i] = CRGB(c.r, c.g, c.b);
        }
    } else {
        fill_solid(g_leds, NUM_LEDS, CRGB::Black);
    }
    FastLED.show();

    const uint32_t elapsed = millis() - now;
    if (elapsed < FRAME_MS) delay(FRAME_MS - elapsed);
}
