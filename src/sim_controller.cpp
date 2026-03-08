#include "sim_controller.h"

SimController::SimController(std::vector<ControllerStrip> strips)
    : _strips(std::move(strips)),
      _expected_gen(_strips.size(), 0)
{
    assert(!_strips.empty() && "SimController requires at least one strip");
    for (size_t i = 0; i < _strips.size(); i++) {
        assert(_strips[i].device && "null device pointer");
        auto [_, inserted] = _strip_id_to_index.emplace(_strips[i].strip_id, i);
        assert(inserted && "duplicate strip_id");
    }
}

void SimController::queue_event(ControllerEvent::Kind kind, const std::string& msg)
{
    ControllerEvent ev;
    ev.kind = kind;
    ev.state = _state;
    ev.session_id = _session_id;
    ev.epoch = _epoch;
    ev.message = msg;
    _events.push_back(std::move(ev));
}

bool SimController::load(const CompiledProgram& program)
{
    // Validate strip count
    if (program.strips.size() != _strips.size()) {
        queue_event(ControllerEvent::ERROR, "strip count mismatch");
        return false;
    }

    // Validate strip IDs and lengths match canonical order
    for (size_t i = 0; i < _strips.size(); i++) {
        if (program.strips[i].strip_id != _strips[i].strip_id) {
            queue_event(ControllerEvent::ERROR,
                "strip id mismatch at index " + std::to_string(i));
            return false;
        }
        if (program.strips[i].length != _strips[i].length) {
            queue_event(ControllerEvent::ERROR,
                "strip length mismatch for " + _strips[i].strip_id);
            return false;
        }
    }

    // Attempt device loads with a provisional gen.
    // Identity (_session_id, _epoch, _gen) is NOT mutated until all succeed.
    uint16_t new_gen = _gen + 1;

    for (size_t i = 0; i < _strips.size(); i++) {
        const auto& sb = program.strips[i];
        if (!_strips[i].device->handle_load(sb.blob.data(), sb.blob.size(), new_gen)) {
            // Roll back already-loaded devices to avoid partial state
            for (size_t j = 0; j < i; j++)
                _strips[j].device->handle_stop();
            queue_event(ControllerEvent::ERROR,
                "device load failed for " + _strips[i].strip_id);
            return false;
        }
    }

    // All devices loaded — commit identity
    _session_id++;
    _epoch = 0;
    _gen = new_gen;
    _duration = program.duration;
    _loop = program.loop;
    _paused_t_rel = 0.0f;
    _buckets.clear();
    for (size_t i = 0; i < _strips.size(); i++)
        _expected_gen[i] = _gen;

    _state = ControllerState::LOADED;
    queue_event(ControllerEvent::SESSION_STARTED);
    queue_event(ControllerEvent::STATE_CHANGED);
    return true;
}

void SimController::play()
{
    if (_state == ControllerState::IDLE || _state == ControllerState::PLAYING)
        return;

    if (_state == ControllerState::PAUSED) {
        // Resume
        int64_t now = _strips[0].device->now_mono();
        int64_t t0 = now - (int64_t)(_paused_t_rel * 1e6f);
        for (auto& s : _strips)
            s.device->handle_resume(t0);
        _state = ControllerState::PLAYING;
        queue_event(ControllerEvent::STATE_CHANGED);
        return;
    }

    // From LOADED, STOPPED, or ENDED
    int64_t t0 = _strips[0].device->now_mono();
    for (auto& s : _strips)
        s.device->handle_start(t0);
    _epoch++;
    _state = ControllerState::PLAYING;
    queue_event(ControllerEvent::STATE_CHANGED);
}

void SimController::pause()
{
    if (_state != ControllerState::PLAYING)
        return;

    for (auto& s : _strips)
        s.device->handle_pause();

    // Capture max t_rel across devices
    _paused_t_rel = 0.0f;
    for (auto& s : _strips) {
        float t = s.device->current_t_rel();
        if (t > _paused_t_rel)
            _paused_t_rel = t;
    }

    _state = ControllerState::PAUSED;
    queue_event(ControllerEvent::STATE_CHANGED);
}

void SimController::seek(float t_rel)
{
    if (_state == ControllerState::IDLE || _state == ControllerState::STOPPED)
        return;

    _epoch++;
    for (auto& s : _strips)
        s.device->debug_seek(t_rel);
    _buckets.clear();

    if (_state == ControllerState::PLAYING) {
        // Stay PLAYING — debug_seek adjusts t0 for PLAYING devices
    } else {
        // LOADED, PAUSED, ENDED → PAUSED
        _paused_t_rel = t_rel;
        _state = ControllerState::PAUSED;
    }
    queue_event(ControllerEvent::STATE_CHANGED);
}

void SimController::stop()
{
    if (_state == ControllerState::IDLE)
        return;

    for (auto& s : _strips)
        s.device->handle_stop();
    _buckets.clear();
    _state = ControllerState::STOPPED;
    queue_event(ControllerEvent::STATE_CHANGED);
}

void SimController::tick_once()
{
    if (_state == ControllerState::IDLE)
        return;

    // 1. Tick each device
    for (auto& s : _strips)
        s.device->tick_once();

    // 2-4. Drain frames, filter by gen, assemble
    for (size_t i = 0; i < _strips.size(); i++) {
        auto frames = _strips[i].device->drain_frames();
        for (auto& frame : frames) {
            if (frame.gen != _expected_gen[i])
                continue;

            auto& bucket = _buckets[frame.frame_index];
            if (bucket.strips.empty()) {
                bucket.frame_index = frame.frame_index;
                bucket.t_rel = frame.t_rel;
                bucket.strips.resize(_strips.size());
            }
            if (bucket.strips[i].empty())
                bucket.present++;
            bucket.strips[i] = std::move(frame.rgb);
        }
    }

    // 5. Emit complete program frames
    auto it = _buckets.begin();
    while (it != _buckets.end()) {
        if (it->second.present == _strips.size()) {
            ProgramFrame pf;
            pf.frame_index = it->second.frame_index;
            pf.t_rel = it->second.t_rel;
            pf.strips = std::move(it->second.strips);
            _program_frames.push_back(std::move(pf));
            it = _buckets.erase(it);
        } else {
            ++it;
        }
    }

    // 6. Check end-of-program
    if (_state == ControllerState::PLAYING) {
        bool all_ended = true;
        for (auto& s : _strips) {
            if (s.device->state() != DeviceState::ENDED) {
                all_ended = false;
                break;
            }
        }

        if (all_ended) {
            if (_loop) {
                _epoch++;
                _gen++;
                _buckets.clear();
                int64_t now = _strips[0].device->now_mono();
                for (size_t i = 0; i < _strips.size(); i++) {
                    _strips[i].device->handle_jump(now, 0.0f, _gen);
                    _strips[i].device->handle_resume(now);
                    _expected_gen[i] = _gen;
                }
                queue_event(ControllerEvent::LOOPED);
            } else {
                _state = ControllerState::ENDED;
                queue_event(ControllerEvent::STATE_CHANGED);
            }
        }
    }
}

ControllerState SimController::state() const
{
    return _state;
}

uint64_t SimController::session_id() const
{
    return _session_id;
}

uint32_t SimController::epoch() const
{
    return _epoch;
}

float SimController::duration() const
{
    return _duration;
}

float SimController::current_t_rel() const
{
    switch (_state) {
        case ControllerState::IDLE:
        case ControllerState::LOADED:
        case ControllerState::STOPPED:
            return 0.0f;
        case ControllerState::PAUSED:
            return _paused_t_rel;
        case ControllerState::PLAYING: {
            float max_t = 0.0f;
            for (auto& s : _strips) {
                float t = s.device->current_t_rel();
                if (t > max_t) max_t = t;
            }
            return max_t;
        }
        case ControllerState::ENDED:
            return _duration;
    }
    return 0.0f;
}

std::vector<ProgramFrame> SimController::drain_program_frames()
{
    std::vector<ProgramFrame> out;
    out.swap(_program_frames);
    return out;
}

std::vector<ControllerEvent> SimController::drain_events()
{
    std::vector<ControllerEvent> out;
    out.swap(_events);
    return out;
}

