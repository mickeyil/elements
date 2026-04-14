#include "engine.h"

#include <cstring>
#include <new>

Engine* Engine::create(Program* program)
{
    Engine* e = new (std::nothrow) Engine(program);
    if (e == nullptr) {
        // Engine struct itself failed to allocate. We still own the Program
        // (the constructor never ran), so free it directly.
        free_program(program);
        return nullptr;
    }
    if (!e->initialize_()) {
        // Engine constructed but its internal allocations failed.
        // ~Engine() will free the Program via free_program(_program).
        delete e;
        return nullptr;
    }
    return e;
}

Engine::Engine(Program* program)
    : _program(program)
{
    // Trivial. All allocations live in initialize_() so failure paths are
    // explicit and create() can clean up cleanly.
}

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

bool Engine::render_frame(float t_program, Strip& out)
{
    if (_program == nullptr) {
        return false;
    }
    if (t_program < 0.0f || t_program >= _program->duration) {
        return false;
    }

    run_copy_ops_until(t_program);

    for (uint8_t li = 0; li < _program->layer_count; li++) {
        _active_dst_views[li] = nullptr;
    }

    for (uint8_t li = 0; li < _program->layer_count; li++) {
        Layer& layer = _program->layers[li];
        LayerPlaybackState& state = _layer_states[li];

        while (state.cursor < layer.event_count) {
            AnimationEvent& e = layer.events[state.cursor];
            const float end = e.start + e.duration;

            if (t_program < e.start) {
                break;
            }

            if (t_program < end) {
                PixelView& dst = _program->pixel_views.at(e.dst_pixv_idx);

                if (!state.initialized) {
                    PixelView* src = (e.src_pixv_idx != PIXV_NONE)
                        ? &_program->pixel_views.at(e.src_pixv_idx)
                        : nullptr;
                    PixelView* work = (e.work_pixv_idx != PIXV_NONE)
                        ? &_program->pixel_views.at(e.work_pixv_idx)
                        : nullptr;

                    e.animation->initialize(src, work);
                    state.initialized = true;
                }

                const float t_animation = t_program - e.start;
                e.animation->render(dst, t_animation);

                // nullptr means inactive. A non-null dst view means this layer
                // contributes to bottom-to-top compositing for this frame.
                _active_dst_views[li] = &dst;
                break;
            }

            state.cursor++;
            state.initialized = false;
        }
    }

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
        _layer_states[li].initialized = false;
    }
    _copy_cursor = 0;

    // All HSVA storage is owned by PixelBufferPool. This clears scratch,
    // stable, source-preservation, and work buffers with one logical pass.
    for (uint16_t bi = 0; bi < _program->pixel_buffer_pool.buffer_count(); bi++) {
        hsva_t* buf = _program->pixel_buffer_pool.buffer_at(bi);
        const uint16_t len = _program->pixel_buffer_pool.buffer_size(bi);
        if (buf != nullptr && len > 0) {
            std::memset(buf, 0, len * sizeof(hsva_t));
        }
    }
}

void Engine::run_copy_ops_until(float t_program)
{
    while (_copy_cursor < _program->copy_ops.count()) {
        const CopyOp& op = _program->copy_ops.at(_copy_cursor);
        if (op.at > t_program) {
            break;
        }

        PixelView& src = _program->pixel_views.at(op.src_pixv_idx);
        PixelView& dst = _program->pixel_views.at(op.dst_pixv_idx);
        copy_view(src, dst);
        _copy_cursor++;
    }
}

void Engine::copy_view(const PixelView& src, PixelView& dst)
{
    if (src.size() != dst.size()) {
        return;
    }

    for (uint16_t i = 0; i < src.size(); i++) {
        dst[i] = src[i];
    }
}
