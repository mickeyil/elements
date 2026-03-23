#include "esp_device.h"

#include <Arduino.h>
#include <FastLED.h>
#include <esp_timer.h>

#include <cstring>

namespace {

constexpr uint8_t LED_PIN = 13;
constexpr uint16_t MAX_DEVICE_PIXELS = 250;

CRGB g_leds[MAX_DEVICE_PIXELS];
bool g_leds_initialized = false;

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
        FastLED.addLeds<WS2812B, LED_PIN, GRB>(g_leds, MAX_DEVICE_PIXELS);
        FastLED.setBrightness(255);
        g_leds_initialized = true;
    }
    fill_solid(g_leds, MAX_DEVICE_PIXELS, CRGB::Black);
    FastLED.show();
}

void esp_device_clear_leds()
{
    if (!g_leds_initialized) {
        return;
    }
    fill_solid(g_leds, MAX_DEVICE_PIXELS, CRGB::Black);
    FastLED.show();
}

ESPDevice::ESPDevice(uint16_t strip_length)
    : PlaybackDevice(strip_length, /*gamma_enabled=*/true)
{
}

int64_t ESPDevice::now_mono() const
{
    return esp_timer_get_time();
}

int64_t ESPDevice::playback_t0(int64_t controller_t0, float target_t_rel) const
{
    (void)controller_t0;
    return now_mono() + _sync_offset - static_cast<int64_t>(target_t_rel * 1e6f);
}

void ESPDevice::output_frame(float t_rel)
{
    memcpy(g_leds, _rgb_buf, _strip_length * 3);
    if (_strip_length < MAX_DEVICE_PIXELS) {
        memset(
            g_leds + _strip_length,
            0,
            (MAX_DEVICE_PIXELS - _strip_length) * sizeof(CRGB)
        );
    }
    FastLED.show();

    EspRgbFrame frame;
    frame.gen = _gen;
    frame.frame_index = _frame_index;
    frame.t_rel = t_rel;
    frame.rgb.assign(_rgb_buf, _rgb_buf + _strip_length * 3);
    _frames.push_back(std::move(frame));
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

std::vector<EspRgbFrame> ESPDevice::drain_frames()
{
    std::vector<EspRgbFrame> out;
    out.swap(_frames);
    return out;
}
