#include "playback_device.h"
#include "engine.h"
#include "decoder.h"
#include "strip.h"

#include <cstring>
#include <algorithm>

PlaybackDevice::PlaybackDevice(uint16_t strip_length, bool gamma_enabled)
    : _strip_length(strip_length), _gamma_enabled(gamma_enabled)
{
    _rgb_buf = new uint8_t[strip_length * 3]();
    _strip = new Strip(_rgb_buf, strip_length);
}

PlaybackDevice::~PlaybackDevice()
{
    delete _engine;
    delete _strip;
    delete[] _rgb_buf;
}

bool PlaybackDevice::handle_load(const uint8_t* blob, size_t blob_len, uint16_t gen)
{
    delete _engine;
    _engine = nullptr;

    Program* prog = decode_program(blob, blob_len);
    if (!prog) {
        _duration = 0.0f;
        memset(_rgb_buf, 0, _strip_length * 3);
        output_frame();
        _state = DeviceState::IDLE;
        send_telemetry(DeviceState::IDLE, 0.0f, "decode failed");
        return false;
    }

    _duration = prog->duration;
    _engine = new Engine(prog, *_strip, _gamma_enabled);
    _gen = gen;
    _frame_index = 0;
    _paused_t_rel = 0.0f;
    memset(_rgb_buf, 0, _strip_length * 3);
    _state = DeviceState::LOADED;
    send_telemetry(DeviceState::LOADED, 0.0f);
    return true;
}

void PlaybackDevice::handle_start(int64_t t0)
{
    if (_state != DeviceState::LOADED && _state != DeviceState::ENDED)
        return;

    if (_state == DeviceState::ENDED)
        _engine->reset();

    _t0 = t0;
    _paused_t_rel = 0.0f;
    _frame_index = 0;
    _state = DeviceState::PLAYING;
    send_telemetry(DeviceState::PLAYING, 0.0f);
}

void PlaybackDevice::handle_jump(int64_t t0, float t_rel, uint16_t gen)
{
    if (!_engine)
        return;

    if (t_rel < 0.0f)
        t_rel = 0.0f;
    if (t_rel >= _duration)
        return;

    DeviceState prev = _state;
    _engine->reset();
    _t0 = t0;
    _gen = gen;
    _frame_index = 0;

    if (prev == DeviceState::PLAYING) {
        // Stay PLAYING — next tick_once renders from new timebase
    } else {
        // LOADED, PAUSED, ENDED → render one frame and pause
        _engine->tick(t_rel);
        output_frame();
        _frame_index++;
        _paused_t_rel = t_rel;
        _state = DeviceState::PAUSED;
    }
}

void PlaybackDevice::handle_pause()
{
    if (_state != DeviceState::PLAYING)
        return;

    _paused_t_rel = current_t_rel();
    _state = DeviceState::PAUSED;
    send_telemetry(DeviceState::PAUSED, _paused_t_rel);
}

void PlaybackDevice::handle_resume(int64_t t0)
{
    if (_state != DeviceState::PAUSED)
        return;

    _t0 = t0;
    _state = DeviceState::PLAYING;
    send_telemetry(DeviceState::PLAYING, _paused_t_rel);
}

void PlaybackDevice::handle_stop()
{
    if (_state == DeviceState::IDLE)
        return;

    _engine->reset();
    memset(_rgb_buf, 0, _strip_length * 3);
    output_frame();
    _frame_index = 0;
    _paused_t_rel = 0.0f;
    _state = DeviceState::LOADED;
    send_telemetry(DeviceState::LOADED, 0.0f);
}

void PlaybackDevice::handle_sync_result(int64_t offset)
{
    _sync_offset = offset;
}

bool PlaybackDevice::tick_once()
{
    if (_state == DeviceState::IDLE || _state == DeviceState::ENDED)
        return false;
    if (_state == DeviceState::LOADED || _state == DeviceState::PAUSED)
        return true;

    // PLAYING
    float t_rel = (float)(now_mono() + _sync_offset - _t0) / 1e6f;
    if (t_rel < 0.0f)
        return true;

    if (!_engine->tick(t_rel)) {
        memset(_rgb_buf, 0, _strip_length * 3);
        output_frame();
        _state = DeviceState::ENDED;
        send_telemetry(DeviceState::ENDED, _duration);
        return false;
    }

    output_frame();
    _frame_index++;
    return true;
}

DeviceState PlaybackDevice::state() const
{
    return _state;
}

float PlaybackDevice::duration() const
{
    return _duration;
}

float PlaybackDevice::current_t_rel() const
{
    switch (_state) {
        case DeviceState::IDLE:
        case DeviceState::LOADED:
            return 0.0f;
        case DeviceState::PAUSED:
            return _paused_t_rel;
        case DeviceState::PLAYING: {
            float t = (float)(now_mono() + _sync_offset - _t0) / 1e6f;
            if (t < 0.0f) return 0.0f;
            if (t > _duration) return _duration;
            return t;
        }
        case DeviceState::ENDED:
            return _duration;
    }
    return 0.0f;
}

const uint8_t* PlaybackDevice::rgb_data() const
{
    return _rgb_buf;
}

uint8_t* PlaybackDevice::rgb_buf()
{
    return _rgb_buf;
}

uint16_t PlaybackDevice::strip_length() const
{
    return _strip_length;
}
