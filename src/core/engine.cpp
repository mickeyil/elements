#include "core/engine.h"

#include <cstring>
#include <new>

Engine* Engine::create(Program* program)
{
    Engine* e = new (std::nothrow) Engine(program);
    if (e == nullptr) {
        // The constructor never ran, so the Program is still ours to free.
        free_program(program);
        return nullptr;
    }
    if (!e->initialize_()) {
        // ~Engine() frees the Program via free_program(_program).
        delete e;
        return nullptr;
    }
    return e;
}

Engine::Engine(Program* program)
    : _program(program)
{}

bool Engine::initialize_()
{
    if (_program == nullptr || _program->layer_count == 0) {
        return true;
    }
    _layer_states = new (std::nothrow) LayerPlaybackState[_program->layer_count]();
    if (_layer_states == nullptr) return false;
    _active_dst_views = new (std::nothrow) PixelView*[_program->layer_count]();
    if (_active_dst_views == nullptr) return false;
    return true;
}

Engine::~Engine()
{
    delete[] _active_dst_views;
    delete[] _layer_states;
    free_program(_program);
}

bool Engine::render_frame(ProgramTime t_program, Strip& out)
{
    if (_program == nullptr) {
        return false;
    }
    if (t_program.ms >= _program->duration.ms) {
        return false;
    }

    advance_to(t_program);
    render_active(t_program);
    _compositor.composite(out, _active_dst_views, _program->layer_count);
    return true;
}

void Engine::reset()
{
    if (_program == nullptr) {
        return;
    }

    for (uint8_t li = 0; li < _program->layer_count; li++) {
        _layer_states[li].cursor = 0;
        _layer_states[li].started = false;
    }
    _copy_cursor = 0;

    for (uint16_t bi = 0; bi < _program->pixel_buffer_pool.buffer_count(); bi++) {
        hsva_t* buf = _program->pixel_buffer_pool.buffer_at(bi);
        const uint16_t len = _program->pixel_buffer_pool.buffer_size(bi);
        if (buf != nullptr && len > 0) {
            std::memset(buf, 0, len * sizeof(hsva_t));
        }
    }
}

void Engine::advance_to(ProgramTime t)
{
    // Each step retires an ending, a copy op, or a start, so the loop
    // always makes progress.
    ProgramTime next;
    while (next_boundary(next) && next <= t) {
        finish_events_ending_at(next);
        run_copy_ops_at(next);
        start_events_starting_at(next);
    }
}

bool Engine::next_boundary(ProgramTime& out) const
{
    bool found = false;
    for (uint8_t li = 0; li < _program->layer_count; li++) {
        const Layer& layer = _program->layers[li];
        const LayerPlaybackState& state = _layer_states[li];
        if (state.cursor >= layer.count()) {
            continue;
        }
        const AnimationEvent& e = layer.at(state.cursor);
        const ProgramTime b = state.started ? e.start + e.duration : e.start;
        if (!found || b < out) {
            out = b;
            found = true;
        }
    }
    if (_copy_cursor < _program->copy_ops.count()) {
        const ProgramTime b = _program->copy_ops.at(_copy_cursor).at;
        if (!found || b < out) {
            out = b;
            found = true;
        }
    }
    return found;
}

void Engine::finish_events_ending_at(ProgramTime t)
{
    for (uint8_t li = 0; li < _program->layer_count; li++) {
        LayerPlaybackState& state = _layer_states[li];
        if (!state.started) {
            continue;
        }
        AnimationEvent& e = _program->layers[li].at(state.cursor);
        if (e.start + e.duration != t) {
            continue;
        }
        e.animation->render(_program->pixel_views.at(e.dst_pixv_idx),
                            seconds(e.duration));
        state.cursor++;
        state.started = false;
    }
}

void Engine::run_copy_ops_at(ProgramTime t)
{
    while (_copy_cursor < _program->copy_ops.count()) {
        const CopyOp& op = _program->copy_ops.at(_copy_cursor);
        if (op.at != t) {
            break;
        }
        copy_view(_program->pixel_views.at(op.src_pixv_idx),
                  _program->pixel_views.at(op.dst_pixv_idx));
        _copy_cursor++;
    }
}

void Engine::start_events_starting_at(ProgramTime t)
{
    for (uint8_t li = 0; li < _program->layer_count; li++) {
        Layer& layer = _program->layers[li];
        LayerPlaybackState& state = _layer_states[li];
        if (state.started || state.cursor >= layer.count()) {
            continue;
        }
        AnimationEvent& e = layer.at(state.cursor);
        if (e.start != t) {
            continue;
        }
        PixelView* src = (e.src_pixv_idx != PIXV_NONE)
            ? &_program->pixel_views.at(e.src_pixv_idx)
            : nullptr;
        PixelView* work = (e.work_pixv_idx != PIXV_NONE)
            ? &_program->pixel_views.at(e.work_pixv_idx)
            : nullptr;
        e.animation->initialize(src, work);
        state.started = true;
    }
}

void Engine::render_active(ProgramTime t)
{
    for (uint8_t li = 0; li < _program->layer_count; li++) {
        _active_dst_views[li] = nullptr;

        const LayerPlaybackState& state = _layer_states[li];
        if (!state.started) {
            continue;
        }
        // Started and not retired, so it runs past t: advance_to(t) has
        // already finished everything ending at or before t.
        AnimationEvent& e = _program->layers[li].at(state.cursor);
        PixelView& dst = _program->pixel_views.at(e.dst_pixv_idx);
        e.animation->render(dst, seconds(t - e.start));
        _active_dst_views[li] = &dst;
    }
}

void Engine::copy_view(const PixelView& src, PixelView& dst)
{
    for (uint16_t i = 0; i < src.size(); i++) {
        dst[i] = src[i];
    }
}
