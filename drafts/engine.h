#pragma once

// Draft-only API.
//
// Engine owns per-layer playback progression and the internal compositor.
// Callers render frames by time; they do not need to know compositor details.

#include <cstdint>

#include "compositor.h"
#include "program_structs.h"
#include "strip.h"

class Engine {
public:
    // Takes ownership of Program.
    explicit Engine(Program* program);
    ~Engine();

    // Advance to t_rel and render the final RGB frame into `out`.
    //
    // Returns false when t_rel is outside the playable program range.
    bool render_frame(float t_rel, Strip& out);

    // Reset per-layer progression and clear mutable program buffers.
    void reset();

private:
    struct LayerPlaybackState {
        // Current event cursor for this layer.
        uint16_t cursor = 0;

        // Whether the active event at `cursor` has already had initialize()
        // run for the current activation.
        bool initialized = false;
    };

    // Owned decoded/runtime program.
    Program* _program = nullptr;

    // Internal final-frame compositor.
    Compositor _compositor;

    // Owned per-layer playback progression state.
    LayerPlaybackState* _layer_states = nullptr;
};
