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

enum class RenderFrameResult : uint8_t {
    // `_strip` was not modified by this call.
    Unchanged,

    // `_strip` contains a newly produced frame.
    Rendered,

    // Natural end-of-program transition. `_strip` was cleared to black and the
    // state changed PLAYING -> ENDED. Returned only for that transition; later
    // calls return Unchanged.
    Ended,
};

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
    RenderFrameResult handle_jump(float t_program, uint16_t gen);
    void handle_pause();
    void handle_resume(int64_t program_start_us);
    RenderFrameResult handle_stop();
    void reset_for_detach();
    RenderFrameResult render_black_frame();

    // Render the next frame accepted by Playback for presentation from the
    // current state and selected clock. "Next" is clock/state-derived, not a
    // fixed frame-index increment.
    RenderFrameResult render_next_frame();

    DeviceState state() const;
    float duration() const;
    // Returns 0 when no program is loaded.
    uint8_t target_fps() const;
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
    uint8_t _target_fps = 0;
    int64_t _program_start_us = 0;
    int64_t _t_program_cursor_us = 0;
    bool _requires_sync = false;

    // TODO: decide whether generation/frame-index metadata belongs here or in
    // the owner. It is omitted from this first-pass sketch.
};
