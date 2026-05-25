#pragma once

#include "playback_device.h"

#include <cstdint>

void esp_device_init_leds();
void esp_device_clear_leds();

class ESPDevice : public PlaybackDevice {
public:
    ESPDevice();
    explicit ESPDevice(uint16_t strip_length);

    int64_t now_mono() const override;

protected:
    void output_frame(float t_rel) override;
    void send_telemetry(DeviceState s, float t, const char* err = nullptr) override;
    int64_t playback_t0(int64_t controller_t0, float target_t_rel) const override;
};
