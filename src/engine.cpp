#include "engine.h"
#include "anim_wave.h"
#include "anim_spark.h"
#include "anim_shift.h"
#include "anim_paint.h"

#include <cstring>

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

static inline void scatter_copy(const hsva_t* src, hsva_t* dst,
                                const uint8_t* remap, uint8_t len)
{
    for (uint8_t i = 0; i < len; i++)
        dst[remap[i]] = src[i];
}

static Animation* create_animation(const AnimationEvent& e, Program* prog,
                                   const LayerDef& dep_layer)
{
    switch (e.params.type) {
        case ANIM_WAVE:
            return new AnimWave(e.params.wave);

        case ANIM_SPARK:
            return new AnimSpark(e.params.spark);

        case ANIM_PAINT:
            return new AnimPaint(e.params.paint);

        case ANIM_SHIFT: {
            hsva_t* work = prog->pool.buffers[e.params.shift.buffer_id];
            uint8_t shift_len = e.remap_is_identity
                ? dep_layer.index_map_length : e.remap_length;

            if (e.source_layer != SOURCE_NONE) {
                const LayerDef& src_layer = prog->layers[e.source_layer];

                // Build physical→source_logical lookup (256 bytes on stack)
                uint8_t src_pos[256];
                memset(src_pos, 0xFF, 256);
                for (uint8_t j = 0; j < src_layer.index_map_length; j++)
                    src_pos[src_layer.index_map[j]] = j;

                // Copy source pixels in dependent's pixel order
                for (uint8_t i = 0; i < shift_len; i++) {
                    uint8_t dep_logical = e.remap_is_identity ? i : e.remap[i];
                    uint8_t physical = dep_layer.index_map[dep_logical];
                    uint8_t si = src_pos[physical];
                    // Compiler guarantees pixel subset — si should never be 0xFF
                    work[i] = (si != 0xFF) ? src_layer.buffer[si] : hsva_t();
                }
            } else {
                memset(work, 0, shift_len * sizeof(hsva_t));
            }

            return new AnimShift(e.params.shift, work, shift_len);
        }

        default:
            return nullptr;
    }
}

// ---------------------------------------------------------------------------
// Engine
// ---------------------------------------------------------------------------

Engine::Engine(Program* prog, Strip& strip)
    : _prog(prog), _compositor(strip), _states(nullptr)
{
    // Allocate layer buffers
    for (uint8_t i = 0; i < prog->layer_count; i++) {
        LayerDef& layer = prog->layers[i];
        layer.buffer = new hsva_t[layer.index_map_length]();
    }

    // Allocate per-layer state
    _states = new LayerState[prog->layer_count];
    for (uint8_t i = 0; i < prog->layer_count; i++) {
        _states[i].cursor = 0;
        _states[i].instance = nullptr;
    }
}

Engine::~Engine()
{
    if (_states) {
        for (uint8_t i = 0; i < _prog->layer_count; i++) {
            delete _states[i].instance;
        }
        delete[] _states;
    }
    free_program(_prog);
}

bool Engine::tick(float t)
{
    if (t >= _prog->duration)
        return false;

    uint32_t active_mask = 0;

    for (uint8_t li = 0; li < _prog->layer_count; li++) {
        LayerDef& layer = _prog->layers[li];
        LayerState& state = _states[li];

        while (state.cursor < layer.event_count) {
            AnimationEvent& e = layer.events[state.cursor];
            float end = e.t_start + e.duration;

            if (t < e.t_start)
                break;  // future event

            if (t < end) {
                // Active — create instance on first frame
                if (!state.instance)
                    state.instance = create_animation(e, _prog, layer);

                float t_rel = t - e.t_start;
                uint8_t len = e.remap_is_identity
                    ? layer.index_map_length : e.remap_length;
                hsva_t* buf = e.remap_is_identity
                    ? layer.buffer : _prog->temp_buffer;

                state.instance->render(buf, len, t_rel);

                if (!e.remap_is_identity)
                    scatter_copy(_prog->temp_buffer, layer.buffer,
                                 e.remap, e.remap_length);
                break;
            }

            // Event finished
            delete state.instance;
            state.instance = nullptr;
            state.cursor++;
        }

        if (state.instance)
            active_mask |= (1u << li);
    }

    _compositor.composite(_prog->layers, _prog->layer_count, active_mask);
    return true;
}
