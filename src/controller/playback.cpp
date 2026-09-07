#include "controller/playback.h"

#include <cmath>

#include "core/decoder.h"

namespace {

constexpr float US_PER_SECOND = 1'000'000.0f;
constexpr int64_t US_PER_MS = 1000;

float t_program_from_us(int64_t t_program_us)
{
    return float(t_program_us) / US_PER_SECOND;
}

int64_t us_from_ms(uint32_t ms)
{
    return static_cast<int64_t>(ms) * US_PER_MS;
}

}  // namespace

Playback::Playback(SyncedClock& clock)
    : _clock(clock)
{
}

Playback::Playback(uint16_t strip_length, SyncedClock& clock)
    : Playback(clock)
{
    apply_hardware_profile(HardwareProfile(strip_length));
}

Playback::~Playback() = default;

bool Playback::has_hardware_profile() const
{
    return _profile.is_valid();
}

const HardwareProfile& Playback::hardware_profile() const
{
    return _profile;
}

bool Playback::apply_hardware_profile(const HardwareProfile& profile)
{
    if (!profile.is_valid()) {
        return false;
    }

    unload_program_();
    reset_program_state_();
    _profile = HardwareProfile();
    if (!_strip.resize(profile.strip_length)) {
        return false;
    }
    _profile = profile;
    return true;
}

bool Playback::handle_load(const uint8_t* blob, size_t blob_len,
                           DecodeError* err_out)
{
    if (!_profile.is_valid()) {
        return false;
    }

    unload_program_();
    reset_program_state_();
    clear_render_buffer_();

    Program* program = decode_program(blob, blob_len, _profile.strip_length,
                                      err_out);
    if (program == nullptr) {
        return false;
    }
    const ProgramDuration duration = program->duration;
    const uint8_t target_fps = program->target_fps;
    const bool requires_sync = program->requires_sync;

    Engine* engine = Engine::create(program);
    if (engine == nullptr) {
        if (err_out != nullptr) {
            *err_out = DecodeError::OutOfMemory;
        }
        return false;
    }

    _duration = duration;
    _target_fps = target_fps;
    _requires_sync = requires_sync;
    _engine.reset(engine);
    _state = DeviceState::LOADED;
    return true;
}

PlaybackResult Playback::handle_start(int64_t program_start_us)
{
    if (_state != DeviceState::LOADED && _state != DeviceState::ENDED) {
        return PlaybackResult::WrongState;
    }
    if (_requires_sync && !_clock.is_synced()) {
        return PlaybackResult::Unsynced;
    }
    if (_state == DeviceState::ENDED && _engine) {
        _engine->reset();
    }

    _program_start_us = _requires_sync ? program_start_us
                                       : _clock.now_local_us();
    _t_program_cursor_us = 0;
    _state = DeviceState::PLAYING;
    return PlaybackResult::Ok;
}

PlaybackResult Playback::handle_jump(float t_program)
{
    if (_engine == nullptr) {
        return PlaybackResult::WrongState;
    }
    if (_state != DeviceState::LOADED && _state != DeviceState::PAUSED) {
        return PlaybackResult::WrongState;
    }
    if (_requires_sync && !_clock.is_synced()) {
        return PlaybackResult::Unsynced;
    }
    // The target arrives as float seconds. Round it half up to the
    // millisecond grid, the same rule the compiler applies to authored
    // times. Non-finite is rejected before the cast (UB on NaN/Inf).
    if (!std::isfinite(t_program) || t_program < 0.0f) {
        return PlaybackResult::BadTime;
    }
    const double target_ms = std::floor(double(t_program) * 1000.0 + 0.5);
    if (target_ms >= double(_duration.ms)) {
        return PlaybackResult::BadTime;
    }

    const int64_t target_us = us_from_ms(static_cast<uint32_t>(target_ms));
    if (target_us <= _t_program_cursor_us) {
        return PlaybackResult::BadTime;
    }

    _engine->reset();
    _t_program_cursor_us = target_us;
    _state = DeviceState::PAUSED;
    return PlaybackResult::Ok;
}

void Playback::handle_pause()
{
    if (_state != DeviceState::PLAYING) {
        return;
    }

    _state = DeviceState::PAUSED;
}

PlaybackResult Playback::handle_resume(int64_t program_start_us)
{
    if (_state != DeviceState::PAUSED) {
        return PlaybackResult::WrongState;
    }
    if (_requires_sync && !_clock.is_synced()) {
        return PlaybackResult::Unsynced;
    }

    _program_start_us = _requires_sync
        ? program_start_us
        : _clock.now_local_us() - _t_program_cursor_us;
    _state = DeviceState::PLAYING;
    return PlaybackResult::Ok;
}

RenderFrameResult Playback::handle_stop()
{
    if (_state == DeviceState::IDLE) {
        return RenderFrameResult::Unchanged;
    }

    if (_engine) {
        _engine->reset();
    }
    clear_render_buffer_();
    reset_timing_state_();
    _state = DeviceState::LOADED;
    return RenderFrameResult::Rendered;
}

void Playback::reset_for_detach()
{
    unload_program_();
    reset_program_state_();
    clear_render_buffer_();
}

RenderFrameResult Playback::render_black_frame()
{
    clear_render_buffer_();
    return RenderFrameResult::Rendered;
}

RenderFrameResult Playback::render_next_frame()
{
    if (_state != DeviceState::PLAYING) {
        return RenderFrameResult::Unchanged;
    }
    if (_engine == nullptr) {
        return RenderFrameResult::Unchanged;
    }

    const int64_t t_program_us = program_clock_now_us() - _program_start_us;
    if (t_program_us < 0) {
        return RenderFrameResult::Unchanged;
    }
    if (t_program_us < _t_program_cursor_us) {
        return RenderFrameResult::Unchanged;
    }
    _t_program_cursor_us = t_program_us;

    // Floor onto the millisecond grid so no boundary renders before the
    // clock reaches it.
    const int64_t t_ms = t_program_us / US_PER_MS;
    const bool rendered = t_ms < int64_t(_duration.ms)
        && _engine->render_frame(ProgramTime{static_cast<uint32_t>(t_ms)}, _strip);
    if (!rendered) {
        clear_render_buffer_();
        _t_program_cursor_us = us_from_ms(_duration.ms);
        _state = DeviceState::ENDED;
        return RenderFrameResult::Ended;
    }
    return RenderFrameResult::Rendered;
}

DeviceState Playback::state() const
{
    return _state;
}

float Playback::duration() const
{
    return seconds(_duration);
}

uint8_t Playback::target_fps() const
{
    return _target_fps;
}

float Playback::current_t_program() const
{
    switch (_state) {
        case DeviceState::IDLE:
        case DeviceState::LOADED:
            return 0.0f;
        case DeviceState::PAUSED:
        case DeviceState::PLAYING: {
            const float t_program = t_program_from_us(_t_program_cursor_us);
            if (t_program > seconds(_duration)) {
                return seconds(_duration);
            }
            return t_program;
        }
        case DeviceState::ENDED:
            return seconds(_duration);
    }
    return 0.0f;
}

uint16_t Playback::strip_length() const
{
    return _strip.size();
}

bool Playback::requires_sync() const
{
    return _requires_sync;
}

Strip& Playback::strip()
{
    return _strip;
}

const Strip& Playback::strip() const
{
    return _strip;
}

int64_t Playback::program_clock_now_us() const
{
    return _requires_sync ? _clock.now_remote_us()
                          : _clock.now_local_us();
}

void Playback::unload_program_()
{
    _engine.reset();
}

void Playback::reset_program_state_()
{
    _state = DeviceState::IDLE;
    _duration = ProgramDuration{};
    _target_fps = 0;
    _requires_sync = false;
    reset_timing_state_();
}

void Playback::reset_timing_state_()
{
    _program_start_us = 0;
    _t_program_cursor_us = 0;
}

void Playback::clear_render_buffer_()
{
    _strip.clear();
}
