#pragma once

#include "playback_device.h"

#include <cstdint>
#include <string>
#include <vector>

struct SimRgbFrame {
    uint16_t gen;
    uint32_t frame_index;
    float t_rel;
    std::vector<uint8_t> rgb;
};

struct SimTelemetry {
    DeviceState state;
    float t_rel;
    std::string error;
};

class ESPSimulated : public PlaybackDevice {
public:
    explicit ESPSimulated(uint16_t strip_length);

    // Drain queued frames/telemetry — destructive, order-preserving
    std::vector<SimRgbFrame> drain_frames();
    std::vector<SimTelemetry> drain_telemetry();

    // Debug extensions (simulator only)
    void debug_seek(float target_t_rel);
    void debug_step(int direction);

protected:
    int64_t now_mono() const override;
    void output_frame() override;
    void send_telemetry(DeviceState s, float t, const char* err = nullptr) override;

private:
    std::vector<SimRgbFrame> _frames;
    std::vector<SimTelemetry> _telemetry;
};
