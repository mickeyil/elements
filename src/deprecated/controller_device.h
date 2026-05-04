#pragma once

#include <cstdint>
#include <cstddef>
#include <vector>

enum class DeviceState : uint8_t;  // defined in playback_device.h

struct DeviceFrame {
    uint16_t gen;
    uint32_t frame_index;
    float t_rel;
    std::vector<uint8_t> rgb;
};

class ControllerDevice {
public:
    virtual ~ControllerDevice() = default;

    // Commands
    virtual bool handle_load(const uint8_t* blob, size_t len, uint16_t gen) = 0;
    virtual void handle_start(int64_t t0) = 0;
    virtual void handle_jump(int64_t t0, float t_rel, uint16_t gen) = 0;
    virtual void handle_pause() = 0;
    virtual void handle_resume(int64_t t0) = 0;
    virtual void handle_stop() = 0;

    // Per-frame tick
    virtual void tick_once() = 0;

    // Queries
    virtual DeviceState state() const = 0;
    virtual float current_t_rel() const = 0;
    virtual int64_t now_mono() const = 0;

    // Frame output
    virtual std::vector<DeviceFrame> drain_frames() = 0;

    // Debug seek capability (sim-only)
    virtual bool supports_debug_seek() const = 0;
    virtual void debug_seek(float t_rel) = 0;
};
