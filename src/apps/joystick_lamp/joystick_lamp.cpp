// Standalone joystick lamp: 16 WS2812B pixels cycling through four
// animations (Pacifica, red alert wave, police strobe, solid soft color),
// controlled by a KY-023 stick. The stick button switches animations;
// horizontal deflection rotates hue and vertical ramps intensity, both at
// a rate proportional to displacement. Power is the wall cord's job. Lamp
// logic lives in lamp_logic for host testing; this file only reads the
// ADC and pushes frames.

#include <Arduino.h>
#include <ArduinoOTA.h>
#include <FastLED.h>
#include <WiFi.h>

#include "apps/joystick_lamp/lamp_logic.h"
#include "platform/wifi_cred_store.h"
#include "platform/esp32/nvs_key_value_store.h"
#include "platform/esp32/wifi_manager.h"
#include "platform/esp32/wifi_provisioning.h"
#include "core/animations/pacifica.h"
#include "core/animations/paint.h"
#include "core/animations/wave.h"
#include "core/colors.h"
#include "core/gamma.h"
#include "core/pixel_view.h"

namespace {

constexpr uint8_t LED_PIN = 13;
constexpr uint16_t NUM_LEDS = 16;

// KY-023 on 3V3: both axes on ADC1 so WiFi can never steal the pins.
constexpr uint8_t PIN_VRX = 34;
constexpr uint8_t PIN_VRY = 35;
constexpr uint8_t PIN_SW = 25;  // press cycles animations

// Signs verified on the assembled lamp: Y comes out inverted.
constexpr float X_SIGN = 1.0f;
constexpr float Y_SIGN = -1.0f;

constexpr uint32_t FRAME_MS = 20;  // ~50 fps
constexpr uint16_t ADC_MAX = 4095;

CRGB g_leds[NUM_LEDS];
hsva_t g_frame[NUM_LEDS];
PixelView g_view;
GammaCorrection g_gamma;

LampState g_lamp;

// Wi-Fi is optional: the lamp lamps regardless. Credentials come from the
// portal (stick button held ~1 s at boot) or a compiled-in secrets.h.
NvsKeyValueStore g_wifi_kv("wifi");
WifiCredStore g_wifi_creds(g_wifi_kv);
WifiManager g_wifi(g_wifi_creds);
char g_host[16];  // "lamp-xxxxxx", also the provisioning AP SSID
bool g_ota_listening = false;

uint16_t g_center_x = ADC_MAX / 2;
uint16_t g_center_y = ADC_MAX / 2;

Pacifica g_pacifica(PacificaParams{1.0f, 1.0f, 0.0f});
// Mirrors animations/ring35_red_alert.py: V swings 0..1 once per second.
Wave g_red_alert(WaveParams{2, 0.0f, 1.0f, 0.0f, 0.0f, 1.0f, 1.0f,
                            float(HALF_PI), 0.0f});
Police g_police;
// Mid saturation so the hue slide reads as a visible tint change.
Paint g_soft(0.0f, 0.6f, 1.0f, 1.0f);

// Order must match LampState::HUE_LOCKED.
Animation* g_anims[LampState::NUM_ANIMS] = {&g_pacifica, &g_red_alert,
                                            &g_police, &g_soft};

const char* g_anim_names[LampState::NUM_ANIMS] = {"pacifica", "red_alert",
                                                  "police", "soft"};

// Median of 5 reads; kills the single-sample spikes these pots produce.
uint16_t read_adc_median(uint8_t pin)
{
    uint16_t s[5];
    for (int i = 0; i < 5; i++) s[i] = analogRead(pin);
    return median5(s);
}

// "lamp-xxxxxx" from the last three eFuse MAC octets, lowercase.
void make_hostname(char* out, size_t cap)
{
    const uint64_t mac = ESP.getEfuseMac();
    snprintf(out, cap, "lamp-%02x%02x%02x", uint8_t(mac >> 24),
             uint8_t(mac >> 32), uint8_t(mac >> 40));
}

// Paint alternating pixels in two colors and leave them showing. Colors
// are passed pre-dimmed; status patterns sit at ~25% intensity.
void show_status_pattern(const CRGB& even, const CRGB& odd)
{
    for (uint16_t i = 0; i < NUM_LEDS; i++) g_leds[i] = (i & 1) ? odd : even;
    FastLED.show();
}

// True if the stick button is held through the first second of boot.
bool button_held_at_boot()
{
    const uint32_t start = millis();
    while (millis() - start < 1000) {
        if (digitalRead(PIN_SW) == HIGH) return false;
        delay(10);
    }
    return true;
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
    // Headroom under the lamp's 2.1 A battery, including the ESP32 itself
    // and Wi-Fi TX bursts.
    FastLED.setMaxPowerInVoltsAndMilliamps(5, 1800);

    pinMode(PIN_SW, INPUT_PULLUP);
    make_hostname(g_host, sizeof(g_host));
    Serial.printf("[init] host=%s\n", g_host);

    // Stick button held through boot: serve the credentials portal instead
    // of the lamp. Saving reboots into normal mode; power cycle abandons.
    if (button_held_at_boot()) {
        Serial.printf("[wifi] provisioning portal up: ssid=%s\n", g_host);
        show_status_pattern(CRGB(0, 0, 64), CRGB(32, 0, 32));
        run_wifi_provisioning_portal(g_host, g_wifi_creds);
    }

    analogReadResolution(12);
    calibrate_center();
    Serial.printf("[init] center x=%u y=%u (stick must be at rest during boot)\n",
                  g_center_x, g_center_y);

    g_view.initialize(g_frame, NUM_LEDS);
    g_pacifica.allocate_scratch(NUM_LEDS);
    // Gamma disabled after a hardware look test: 2.6 crushed the animations.
    // The identity LUT stays in the pipeline; set_gamma() here re-enables.
    g_gamma.set_identity();

    // Wi-Fi comes up in the background; the lamp never waits for it.
    g_wifi.begin();
    ArduinoOTA.setHostname(g_host);
    ArduinoOTA.onStart([] {
        Serial.println("[ota] start");
        show_status_pattern(CRGB(64, 0, 0), CRGB(32, 0, 32));
    });
    ArduinoOTA.onEnd([] { Serial.println("[ota] done, rebooting"); });
    ArduinoOTA.onError([](ota_error_t e) {
        Serial.printf("[ota] error %u\n", unsigned(e));
    });
}

void loop()
{
    static bool s_first = true;
    static uint32_t s_prev_ms = 0;
    static uint32_t s_last_stick_log = 0;

    const uint32_t now = millis();
    const uint32_t dt = s_first ? 0 : now - s_prev_ms;
    s_prev_ms = now;
    if (s_first) {
        s_first = false;
        Serial.println("[loop] start");
    }

    const NetworkTransition net = g_wifi.poll();
    if (net == NetworkTransition::link_up) {
        Serial.printf("[wifi] up %s\n", WiFi.localIP().toString().c_str());
        // Restart the listener so mDNS re-registers after a reconnect.
        if (g_ota_listening) ArduinoOTA.end();
        ArduinoOTA.begin();
        g_ota_listening = true;
        Serial.printf("[ota] listening as %s.local\n", g_host);
    } else if (net == NetworkTransition::link_down) {
        Serial.println("[wifi] down");
    }
    if (g_wifi.is_up()) ArduinoOTA.handle();

    const uint16_t raw_x = read_adc_median(PIN_VRX);
    const uint16_t raw_y = read_adc_median(PIN_VRY);
    const float x = X_SIGN * normalize_stick(raw_x, g_center_x, ADC_MAX);
    const float y = Y_SIGN * normalize_stick(raw_y, g_center_y, ADC_MAX);

    // Button press cycles animations; the 30 ms guard debounces both edges.
    static bool s_sw_pressed = false;
    static uint32_t s_sw_change_ms = 0;
    const bool sw_now = digitalRead(PIN_SW) == LOW;  // pullup: LOW = pressed
    if (sw_now != s_sw_pressed && now - s_sw_change_ms >= 30) {
        s_sw_pressed = sw_now;
        s_sw_change_ms = now;
        if (sw_now) {
            g_lamp.next_anim();
            Serial.printf("[lamp] anim -> %d (%s)\n", g_lamp.anim_index(),
                          g_anim_names[g_lamp.anim_index()]);
        }
    }

    g_lamp.update(x, y, dt);

    // Trace deflections above half the deadzone so wrong signs and
    // off-center calibration are visible without spamming at rest.
    if ((fabsf(x) > 0.10f || fabsf(y) > 0.10f) &&
        now - s_last_stick_log >= 200) {
        s_last_stick_log = now;
        Serial.printf("[stick] x=%+.2f y=%+.2f raw=%u/%u hue=%.0f "
                      "intensity=%.2f\n",
                      x, y, raw_x, raw_y, g_lamp.hue_for_current(),
                      g_lamp.intensity());
    }

    g_anims[g_lamp.anim_index()]->render(g_view, now / 1000.0f);
    const float hue = g_lamp.hue_for_current();
    const float intensity = g_lamp.intensity();
    for (uint16_t i = 0; i < NUM_LEDS; i++) {
        const hsva_t& p = g_frame[i];
        const rgb_t c = g_gamma.correct(
            hsv_to_rgb(wrap360(p.h + hue), p.s, p.v * intensity));
        g_leds[i] = CRGB(c.r, c.g, c.b);
    }
    FastLED.show();

    const uint32_t elapsed = millis() - now;
    if (elapsed < FRAME_MS) delay(FRAME_MS - elapsed);
}
