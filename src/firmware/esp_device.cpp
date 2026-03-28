#include "esp_device.h"

#include <Arduino.h>
#include <FastLED.h>
#include <esp_timer.h>

#include <cstring>

namespace {

constexpr uint8_t LED_PIN = 13;

CRGB g_leds[kMaxStripPixels];
bool g_leds_initialized = false;
static_assert(sizeof(CRGB) == 3, "ESPDevice assumes packed CRGB layout");

const char* device_state_name(DeviceState state)
{
    switch (state) {
        case DeviceState::IDLE:
            return "IDLE";
        case DeviceState::LOADED:
            return "LOADED";
        case DeviceState::PLAYING:
            return "PLAYING";
        case DeviceState::PAUSED:
            return "PAUSED";
        case DeviceState::ENDED:
            return "ENDED";
    }
    return "UNKNOWN";
}

}  // namespace

void esp_device_init_leds()
{
    if (!g_leds_initialized) {
        FastLED.addLeds<WS2812B, LED_PIN, GRB>(g_leds, kMaxStripPixels);
        FastLED.setBrightness(255);
        g_leds_initialized = true;
    }
    fill_solid(g_leds, kMaxStripPixels, CRGB::Black);
    FastLED.show();
}

void esp_device_clear_leds()
{
    if (!g_leds_initialized) {
        return;
    }
    fill_solid(g_leds, kMaxStripPixels, CRGB::Black);
    FastLED.show();
}

ESPDevice::ESPDevice()
    : PlaybackDevice(/*gamma_enabled=*/true)
{
}

ESPDevice::ESPDevice(uint16_t strip_length)
    : ESPDevice()
{
    apply_hardware_profile(HardwareProfile{strip_length});
}

int64_t ESPDevice::now_mono() const
{
    return esp_timer_get_time();
}

int64_t ESPDevice::playback_t0(int64_t controller_t0, float target_t_rel) const
{
    if (sync_valid()) {
        return controller_t0;
    }
    return now_mono() - static_cast<int64_t>(target_t_rel * 1e6f);
}

void ESPDevice::output_frame(float t_rel)
{
    (void)t_rel;
    const uint16_t length = strip_length();
    const uint8_t* rgb = rgb_data();

    memcpy(g_leds, rgb, length * 3);
    if (length < kMaxStripPixels) {
        memset(
            g_leds + length,
            0,
            (kMaxStripPixels - length) * sizeof(CRGB)
        );
    }
    FastLED.show();
}

void ESPDevice::send_telemetry(DeviceState state, float t_rel, const char* err)
{
    if (err && err[0] != '\0') {
        Serial.printf(
            "[device] state=%s t=%.3f err=%s\n",
            device_state_name(state),
            t_rel,
            err
        );
        return;
    }

    Serial.printf(
        "[device] state=%s t=%.3f\n",
        device_state_name(state),
        t_rel
    );
}
