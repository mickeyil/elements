#include "engine.h"

#include <cstring>

Engine::Engine(Program* program)
    : _program(program)
{
    if (_program != nullptr && _program->layer_count > 0) {
        _layer_states = new LayerPlaybackState[_program->layer_count]();
    }
}

Engine::~Engine()
{
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

    uint32_t active_mask = 0;

    for (uint8_t li = 0; li < _program->layer_count; li++) {
        Layer& layer = _program->layers[li];
        LayerPlaybackState& state = _layer_states[li];

        while (state.cursor < layer.event_count) {
            AnimationEvent& e = layer.events[state.cursor];
            const float end = e.t_start + e.duration;

            if (t_rel < e.t_start) {
                break;
            }

            if (t_rel < end) {
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

                PixelView& dst = _program->pixel_views.at(e.dst_pixv_idx);
                e.animation->render(dst, t_rel - e.t_start);

                // Layer activity is derived directly from event timing, not
                // from any old instance pointer or from the initialized flag.
                active_mask |= (1u << li);
                break;
            }

            state.cursor++;
            state.initialized = false;
        }
    }

    _compositor.composite(out, _program->layers, _program->layer_count, active_mask);
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

    // Canonical layer buffers are pool buffers, so the pool-wide clear below
    // already covers them. Avoid clearing them twice.
    for (uint16_t bi = 0; bi < _program->pixel_buffer_pool.buffer_count(); bi++) {
        hsva_t* buf = _program->pixel_buffer_pool.buffer_at(bi);
        const uint16_t len = _program->pixel_buffer_pool.buffer_size(bi);
        if (buf != nullptr && len > 0) {
            std::memset(buf, 0, len * sizeof(hsva_t));
        }
    }
}
