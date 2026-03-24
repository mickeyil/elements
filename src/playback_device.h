#pragma once

#include <cstdint>
#include <cstddef>

class Engine;
class Strip;

enum class DeviceState : uint8_t { IDLE, LOADED, PLAYING, PAUSED, ENDED };

class PlaybackDevice {
public:
    PlaybackDevice(uint16_t strip_length, bool gamma_enabled = true);
    virtual ~PlaybackDevice();

    // Command handlers
    bool handle_load(const uint8_t* blob, size_t blob_len, uint16_t gen);
    void handle_start(int64_t t0);
    void handle_jump(int64_t t0, float t_rel, uint16_t gen);
    void handle_pause();
    void handle_resume(int64_t t0);
    void handle_stop();
    void handle_sync_result(int64_t offset);
    void clear_sync();

    // Per-frame tick — call in main loop
    bool tick_once();

    // Accessors
    DeviceState state() const;
    float duration() const;
    float current_t_rel() const;
    const uint8_t* rgb_data() const;
    uint8_t* rgb_buf();
    uint16_t strip_length() const;
    bool sync_valid() const;
    bool playback_uses_sync() const;

    // Platform-specific — subclasses override
    virtual int64_t now_mono() const = 0;

protected:
    virtual void output_frame(float t_rel) = 0;
    virtual void send_telemetry(DeviceState s, float t, const char* err = nullptr) {}
    virtual int64_t playback_t0(int64_t controller_t0, float target_t_rel) const;

    uint16_t _strip_length;
    uint8_t* _rgb_buf;
    Strip* _strip;
    Engine* _engine = nullptr;
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
};
