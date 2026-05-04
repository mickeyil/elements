#include "esp_simulated.h"
#include "engine.h"

#include <chrono>

ESPSimulated::ESPSimulated()
    : PlaybackDevice(/*gamma_enabled=*/false)
{
}

ESPSimulated::ESPSimulated(uint16_t strip_length)
    : ESPSimulated()
{
    apply_hardware_profile(HardwareProfile{strip_length});
}

int64_t ESPSimulated::now_mono() const
{
    auto now = std::chrono::steady_clock::now();
    return std::chrono::duration_cast<std::chrono::microseconds>(
        now.time_since_epoch()).count();
}

void ESPSimulated::output_frame(float t_rel)
{
    const uint16_t length = strip_length();
    const uint8_t* rgb = rgb_data();

    SimRgbFrame f;
    f.gen = _gen;
    f.frame_index = _frame_index;
    f.t_rel = t_rel;
    f.rgb.assign(rgb, rgb + length * 3);
    _frames.push_back(std::move(f));
}

void ESPSimulated::send_telemetry(DeviceState s, float t, const char* err)
{
    _telemetry.push_back({s, t, err ? err : ""});
}

void ESPSimulated::clear_queued_runtime_outputs()
{
    _frames.clear();
    _telemetry.clear();
}

std::vector<SimRgbFrame> ESPSimulated::drain_frames()
{
    std::vector<SimRgbFrame> out;
    out.swap(_frames);
    return out;
}

std::vector<SimTelemetry> ESPSimulated::drain_telemetry()
{
    std::vector<SimTelemetry> out;
    out.swap(_telemetry);
    return out;
}

void ESPSimulated::debug_seek(float target_t_rel)
{
    if (!_engine)
        return;

    float dur = duration();
    if (target_t_rel < 0.0f) target_t_rel = 0.0f;
    if (target_t_rel > dur) target_t_rel = dur;

    _engine->reset();
    float dt = 1.0f / 50.0f;
    for (float t = dt; t < target_t_rel; t += dt)
        _engine->tick(t);
    _engine->tick(target_t_rel);

    output_frame(target_t_rel);
    _frame_index++;

    if (_state == DeviceState::PLAYING) {
        const int64_t effective_offset = _playback_uses_sync ? _sync_offset : 0;
        _t0 = now_mono() - effective_offset - (int64_t)(target_t_rel * 1e6f);
    } else {
        _paused_t_rel = target_t_rel;
        _state = DeviceState::PAUSED;
    }
}

void ESPSimulated::debug_step(int direction)
{
    if (_state != DeviceState::PAUSED && _state != DeviceState::LOADED)
        return;

    float dur = duration();
    float target = _paused_t_rel + direction * (1.0f / 50.0f);
    if (target < 0.0f) target = 0.0f;
    if (target > dur) target = dur;

    _engine->reset();
    float dt = 1.0f / 50.0f;
    for (float t = dt; t < target; t += dt)
        _engine->tick(t);
    _engine->tick(target);

    _paused_t_rel = target;
    _state = DeviceState::PAUSED;
    output_frame(target);
    _frame_index++;
}
