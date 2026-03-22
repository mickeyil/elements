#pragma once

#include "playback_device.h"

#include <cstdint>
#include <vector>

struct EspRgbFrame {
    uint16_t gen;
    uint32_t frame_index;
    float t_rel;
    std::vector<uint8_t> rgb;
};

void esp_device_init_leds();
void esp_device_clear_leds();

class ESPDevice : public PlaybackDevice {
public:
    explicit ESPDevice(uint16_t strip_length);

    std::vector<EspRgbFrame> drain_frames();

    int64_t now_mono() const override;

protected:
    void output_frame(float t_rel) override;
    void send_telemetry(DeviceState s, float t, const char* err = nullptr) override;

private:
    std::vector<EspRgbFrame> _frames;
};
