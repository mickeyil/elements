#include "playback.h"

#include <cstring>

Playback::Playback(DeviceClock& clock)
    : _clock(clock)
{
}

Playback::Playback(uint16_t strip_length, DeviceClock& clock)
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
        clear_render_buffer_();
        return false;
    }
    _profile = profile;
    return true;
}

bool Playback::handle_load(const uint8_t* blob, size_t blob_len, uint16_t gen)
{
    (void)blob;
    (void)blob_len;
    (void)gen;

    if (!_profile.is_valid()) {
        return false;
    }

    unload_program_();
    reset_program_state_();

    // TODO: decode the blob and build the engine. See drafts/decoder.md
    // "Integration with Playback" for the exact shape. The implementation
    // sketch is:
    //
    //   DecodeError err = DecodeError::Ok;
    //   Program* program = decode_program(blob, blob_len,
    //                                     _profile.strip_length, &err);
    //   if (program == nullptr) {
    //       clear_render_buffer_();
    //       // log decode_error_name(err)
    //       return false;
    //   }
    //   const float duration_s = program->duration;
    //   const bool  needs_sync = program->requires_sync;
    //   Engine* engine = Engine::create(program);  // takes ownership
    //   if (engine == nullptr) {
    //       clear_render_buffer_();
    //       // log "[load] engine alloc failed"
    //       return false;
    //   }
    //   _duration      = duration_s;
    //   _requires_sync = needs_sync;
    //   _engine.reset(engine);
    //   clear_render_buffer_();
    //   _state = DeviceState::LOADED;
    //   return true;
    Program* program = nullptr;
    if (program == nullptr) {
        clear_render_buffer_();
        return false;
    }
    _state = DeviceState::LOADED;
    return true;
}

void Playback::handle_start(int64_t program_start_us)
{
    if (_state != DeviceState::LOADED && _state != DeviceState::ENDED) {
        return;
    }
    if (_requires_sync && !_clock.is_synced()) {
        return;
    }
    if (_state == DeviceState::ENDED && _engine) {
        _engine->reset();
    }

    _program_start_us = program_start_us;
    _paused_t_program = 0.0f;
    _state = DeviceState::PLAYING;
}

void Playback::handle_jump(int64_t program_start_us, float t_program, uint16_t gen)
{
    (void)gen;

    if (_engine == nullptr) {
        return;
    }
    if (_requires_sync && !_clock.is_synced()) {
        return;
    }
    if (t_program < 0.0f || t_program >= _duration) {
        return;
    }

    DeviceState prev = _state;
    _engine->reset();
    _program_start_us = program_start_us;

    if (prev == DeviceState::PLAYING) {
        // Mirror current runtime behavior: when jumping during active playback,
        // retime immediately and let the next tick_once() render from the new
        // position. Non-playing states render a frame right away because the
        // owner expects a paused/loaded frame to be available immediately.
        return;
    }

    _engine->render_frame(t_program, _strip);
    _paused_t_program = t_program;
    _state = DeviceState::PAUSED;
}

void Playback::handle_pause()
{
    if (_state != DeviceState::PLAYING) {
        return;
    }

    _paused_t_program = current_t_program();
    _state = DeviceState::PAUSED;
}

void Playback::handle_resume(int64_t program_start_us)
{
    if (_state != DeviceState::PAUSED) {
        return;
    }
    if (_requires_sync && !_clock.is_synced()) {
        return;
    }

    _program_start_us = program_start_us;
    _state = DeviceState::PLAYING;
}

void Playback::handle_stop()
{
    if (_state == DeviceState::IDLE) {
        return;
    }

    if (_engine) {
        _engine->reset();
    }
    clear_render_buffer_();
    reset_timing_state_();
    _state = DeviceState::LOADED;
}

void Playback::reset_for_detach()
{
    unload_program_();
    reset_program_state_();
    clear_render_buffer_();
}

void Playback::present_black_frame()
{
    clear_render_buffer_();
}

bool Playback::tick_once()
{
    if (_state == DeviceState::IDLE || _state == DeviceState::ENDED) {
        return false;
    }
    if (_state == DeviceState::LOADED || _state == DeviceState::PAUSED) {
        return true;
    }
    if (_engine == nullptr) {
        return false;
    }

    const float t_program =
        float(program_clock_now_us() - _program_start_us) / 1e6f;
    if (t_program < 0.0f) {
        return true;
    }
    if (!_engine->render_frame(t_program, _strip)) {
        clear_render_buffer_();
        _state = DeviceState::ENDED;
        return false;
    }
    return true;
}

DeviceState Playback::state() const
{
    return _state;
}

float Playback::duration() const
{
    return _duration;
}

float Playback::current_t_program() const
{
    switch (_state) {
        case DeviceState::IDLE:
        case DeviceState::LOADED:
            return 0.0f;
        case DeviceState::PAUSED:
            return _paused_t_program;
        case DeviceState::PLAYING: {
            float t_program =
                float(program_clock_now_us() - _program_start_us) / 1e6f;
            if (t_program < 0.0f) {
                return 0.0f;
            }
            if (t_program > _duration) {
                return _duration;
            }
            return t_program;
        }
        case DeviceState::ENDED:
            return _duration;
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
    return _requires_sync ? _clock.now_controller_us()
                          : _clock.now_local_us();
}

void Playback::unload_program_()
{
    _engine.reset();
}

void Playback::reset_program_state_()
{
    _state = DeviceState::IDLE;
    _duration = 0.0f;
    _requires_sync = false;
    reset_timing_state_();
}

void Playback::reset_timing_state_()
{
    _program_start_us = 0;
    _paused_t_program = 0.0f;
}

void Playback::clear_render_buffer_()
{
    _strip.clear();
}
