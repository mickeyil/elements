#pragma once

// Draft-only API.
//
// Playback is the concrete state machine around Engine. It owns timing,
// program lifecycle, and the canonical RGB Strip used for the latest frame.

#include <cstddef>
#include <cstdint>
#include <memory>

#include "engine.h"
#include "hardware_profile.h"
#include "strip.h"
#include "device_clock.h"

enum class DeviceState : uint8_t { IDLE, LOADED, PLAYING, PAUSED, ENDED };

class Playback {
public:
    explicit Playback(DeviceClock& clock);
    Playback(uint16_t strip_length, DeviceClock& clock);
    ~Playback();

    bool has_hardware_profile() const;
    const HardwareProfile& hardware_profile() const;
    bool apply_hardware_profile(const HardwareProfile& profile);

    bool handle_load(const uint8_t* blob, size_t blob_len, uint16_t gen);
    void handle_start(int64_t program_start_us);
    void handle_jump(int64_t program_start_us, float t_program, uint16_t gen);
    void handle_pause();
    void handle_resume(int64_t program_start_us);
    void handle_stop();
    void reset_for_detach();
    void present_black_frame();

    // Advance according to the currently selected clock domain and render the
    // latest frame into `_strip`.
    bool tick_once();

    DeviceState state() const;
    float duration() const;
    float current_t_program() const;
    uint16_t strip_length() const;
    bool requires_sync() const;

    Strip& strip();
    const Strip& strip() const;

private:
    int64_t program_clock_now_us() const;
    void unload_program_();
    void reset_program_state_();
    void reset_timing_state_();
    void clear_render_buffer_();

    // Shared concrete clock abstraction.
    DeviceClock& _clock;

    // Owned canonical RGB frame buffer for the latest rendered frame.
    Strip _strip;

    // Owned render core for the currently loaded program.
    std::unique_ptr<Engine> _engine;

    // Hardware/output profile selected by the owner.
    HardwareProfile _profile{};

    DeviceState _state = DeviceState::IDLE;
    float _duration = 0.0f;
    int64_t _program_start_us = 0;
    float _paused_t_program = 0.0f;
    bool _requires_sync = false;

    // TODO: decide whether generation/frame-index metadata belongs here or in
    // the owner. It is omitted from this first-pass sketch.
};
