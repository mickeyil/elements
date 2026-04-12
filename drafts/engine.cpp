#include "engine.h"

#include <cstring>

Engine::Engine(Program* program)
    : _program(program)
{
    if (_program != nullptr && _program->layer_count > 0) {
        _layer_states = new LayerPlaybackState[_program->layer_count]();
        _active_dst_views = new PixelView*[_program->layer_count]();
    }
    if (_program != nullptr && _program->copy_ops.count() > 0) {
        _copy_done = new bool[_program->copy_ops.count()]();
    }
}

Engine::~Engine()
{
    delete[] _copy_done;
    delete[] _active_dst_views;
    delete[] _layer_states;
    free_program_sketch(_program);
}

bool Engine::render_frame(float t_rel, Strip& out)
{
    if (_program == nullptr) {
        return false;
    }
    if (t_rel < 0.0f || t_rel >= _program->duration) {
        return false;
    }

    for (uint8_t li = 0; li < _program->layer_count; li++) {
        _active_dst_views[li] = nullptr;
    }

    for (uint8_t li = 0; li < _program->layer_count; li++) {
        run_copy_ops_for_stage(t_rel, li);

        Layer& layer = _program->layers[li];
        LayerPlaybackState& state = _layer_states[li];

        while (state.cursor < layer.event_count) {
            AnimationEvent& e = layer.events[state.cursor];
            const float end = e.t_start + e.duration;

            if (t_rel < e.t_start) {
                break;
            }

            if (t_rel < end) {
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

                e.animation->render(dst, t_rel - e.t_start);

                // nullptr means inactive. A non-null dst view means this layer
                // contributes to bottom-to-top compositing for this frame.
                _active_dst_views[li] = &dst;
                break;
            }

            state.cursor++;
            state.initialized = false;
        }
    }
    run_copy_ops_for_stage(t_rel, _program->layer_count);

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
    for (uint16_t ci = 0; ci < _program->copy_ops.count(); ci++) {
        _copy_done[ci] = false;
    }

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

void Engine::run_copy_ops_for_stage(float t_rel, uint8_t before_layer_idx)
{
    for (uint16_t ci = 0; ci < _program->copy_ops.count(); ci++) {
        if (_copy_done[ci]) {
            continue;
        }

        const CopyOp& op = _program->copy_ops.at(ci);
        if (op.before_layer_idx != before_layer_idx || op.at > t_rel) {
            continue;
        }

        PixelView& src = _program->pixel_views.at(op.src_pixv_idx);
        PixelView& dst = _program->pixel_views.at(op.dst_pixv_idx);
        copy_view(src, dst);
        _copy_done[ci] = true;
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
