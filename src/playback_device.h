#pragma once

#include "hardware_profile.h"

#include <array>
#include <cstdint>
#include <cstddef>
#include <memory>

#include "strip.h"

class Engine;

enum class DeviceState : uint8_t { IDLE, LOADED, PLAYING, PAUSED, ENDED };

class PlaybackDevice {
public:
    explicit PlaybackDevice(bool gamma_enabled = true);
    PlaybackDevice(uint16_t strip_length, bool gamma_enabled = true);
    virtual ~PlaybackDevice();

    bool has_hardware_profile() const;
    const HardwareProfile& hardware_profile() const;
    bool apply_hardware_profile(const HardwareProfile& profile);

    // Command handlers
    bool handle_load(const uint8_t* blob, size_t blob_len, uint16_t gen);
    void handle_start(int64_t t0);
    void handle_jump(int64_t t0, float t_rel, uint16_t gen);
    void handle_pause();
    void handle_resume(int64_t t0);
    void handle_stop();
    void handle_sync_result(int64_t offset);
    void clear_sync();
    void reset_for_detach();

    // Per-frame tick — call in main loop
    bool tick_once();

    // Accessors
    DeviceState state() const;
    float duration() const;
    float current_t_rel() const;
    const uint8_t* rgb_data() const;
    uint16_t strip_length() const;
    bool sync_valid() const;
    bool playback_uses_sync() const;

    // Platform-specific — subclasses override
    virtual int64_t now_mono() const = 0;

protected:
    virtual void output_frame(float t_rel) = 0;
    virtual void send_telemetry(DeviceState s, float t, const char* err = nullptr) {}
    virtual int64_t playback_t0(int64_t controller_t0, float target_t_rel) const;
    virtual void clear_queued_runtime_outputs() {}

    std::array<uint8_t, kMaxStripPixels * 3> _rgb_storage{};
    Strip _strip;
    std::unique_ptr<Engine> _engine;
    HardwareProfile _profile{};
    DeviceState _state = DeviceState::IDLE;
    float _duration = 0.0f;
    int64_t _t0 = 0;
    int64_t _sync_offset = 0;
    bool _sync_valid = false;
    bool _playback_uses_sync = false;
    uint16_t _gen = 0;
    uint32_t _frame_index = 0;
    float _paused_t_rel = 0.0f;
    bool _gamma_enabled;

private:
    void unload_program_();
    void reset_program_state_();
    void reset_timing_state_();
    void reset_sync_state_();
    void clear_render_buffer_();
};
