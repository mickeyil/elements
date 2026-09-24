#include "controller/playback.h"

#include <cstring>

#include "core/decoder.h"

namespace {

constexpr int64_t US_PER_MS = 1000;

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
    const bool loop = program->loop;

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
    _loop = loop;
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
    _engine_cycle = 0;
    _state = DeviceState::PLAYING;
    return PlaybackResult::Ok;
}

PlaybackResult Playback::handle_jump(uint32_t t_ms)
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
    if (t_ms >= _duration.ms) {
        return PlaybackResult::BadTime;
    }

    // A looping program jumps within the cycle the cursor is in.
    const int64_t cycle = _loop ? _t_program_cursor_us / duration_us_() : 0;
    const int64_t target_us = cycle * duration_us_() + us_from_ms(t_ms);
    if (target_us <= _t_program_cursor_us) {
        return PlaybackResult::BadTime;
    }

    _engine->reset();
    _engine_cycle = cycle;
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
    _state = _engine ? DeviceState::LOADED : DeviceState::IDLE;
    return RenderFrameResult::Rendered;
}

bool Playback::handle_manual(const uint8_t* rgb, size_t len)
{
    if (!_profile.is_valid() || len != _strip.byte_size()) {
        return false;
    }

    unload_program_();
    reset_program_state_();
    std::memcpy(_strip.bytes(), rgb, len);
    _present_pending = true;
    _state = DeviceState::MANUAL;
    return true;
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
    int64_t t_ms = t_program_us / US_PER_MS;
    if (_loop) {
        // Entering a new cycle, or skipping whole ones, rewinds the engine
        // before this frame renders: the wrap shows no black frame.
        const int64_t cycle = t_ms / _duration.ms;
        t_ms %= _duration.ms;
        if (cycle != _engine_cycle) {
            _engine->reset();
            _engine_cycle = cycle;
        }
    }
    const bool rendered = t_ms < int64_t(_duration.ms)
        && _engine->render_frame(ProgramTime{static_cast<uint32_t>(t_ms)}, _strip);
    if (!rendered) {
        clear_render_buffer_();
        _t_program_cursor_us = us_from_ms(_duration.ms);
        _state = DeviceState::ENDED;
        return RenderFrameResult::Ended;
    }
    _present_pending = true;
    return RenderFrameResult::Rendered;
}

bool Playback::take_present_request()
{
    const bool pending = _present_pending;
    _present_pending = false;
    return pending;
}

DeviceState Playback::state() const
{
    return _state;
}

uint32_t Playback::duration_ms() const
{
    return _duration.ms;
}

uint8_t Playback::target_fps() const
{
    return _target_fps;
}

uint32_t Playback::current_cycle() const
{
    if (!_loop || (_state != DeviceState::PAUSED && _state != DeviceState::PLAYING)) {
        return 0;
    }
    return static_cast<uint32_t>(_t_program_cursor_us / duration_us_());
}

uint32_t Playback::current_t_ms() const
{
    switch (_state) {
        case DeviceState::IDLE:
        case DeviceState::LOADED:
        case DeviceState::MANUAL:
            return 0;
        case DeviceState::PAUSED:
        case DeviceState::PLAYING:
            if (_loop) {
                return static_cast<uint32_t>(
                    (_t_program_cursor_us % duration_us_()) / US_PER_MS);
            }
            if (_t_program_cursor_us >= duration_us_()) {
                return _duration.ms;
            }
            return static_cast<uint32_t>(_t_program_cursor_us / US_PER_MS);
        case DeviceState::ENDED:
            return _duration.ms;
    }
    return 0;
}

uint16_t Playback::strip_length() const
{
    return _strip.size();
}

bool Playback::requires_sync() const
{
    return _requires_sync;
}

bool Playback::loop() const
{
    return _loop;
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

int64_t Playback::duration_us_() const
{
    return us_from_ms(_duration.ms);
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
    _loop = false;
    reset_timing_state_();
}

void Playback::reset_timing_state_()
{
    _program_start_us = 0;
    _t_program_cursor_us = 0;
    _engine_cycle = 0;
}

void Playback::clear_render_buffer_()
{
    _strip.clear();
    _present_pending = true;
}
