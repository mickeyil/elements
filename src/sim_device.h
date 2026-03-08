#pragma once

#include "controller_device.h"
#include "esp_simulated.h"

class SimDevice : public ControllerDevice {
public:
    explicit SimDevice(ESPSimulated& device) : _dev(device) {}

    bool handle_load(const uint8_t* blob, size_t len, uint16_t gen) override {
        return _dev.handle_load(blob, len, gen);
    }
    void handle_start(int64_t t0) override { _dev.handle_start(t0); }
    void handle_jump(int64_t t0, float t_rel, uint16_t gen) override {
        _dev.handle_jump(t0, t_rel, gen);
    }
    void handle_pause() override { _dev.handle_pause(); }
    void handle_resume(int64_t t0) override { _dev.handle_resume(t0); }
    void handle_stop() override { _dev.handle_stop(); }

    void tick_once() override { _dev.tick_once(); }

    DeviceState state() const override { return _dev.state(); }
    float current_t_rel() const override { return _dev.current_t_rel(); }
    int64_t now_mono() const override { return _dev.now_mono(); }

    std::vector<DeviceFrame> drain_frames() override {
        auto sim_frames = _dev.drain_frames();
        std::vector<DeviceFrame> out;
        out.reserve(sim_frames.size());
        for (auto& sf : sim_frames)
            out.push_back({sf.gen, sf.frame_index, sf.t_rel, std::move(sf.rgb)});
        return out;
    }

    bool supports_debug_seek() const override { return true; }
    void debug_seek(float t_rel) override { _dev.debug_seek(t_rel); }

private:
    ESPSimulated& _dev;
};
