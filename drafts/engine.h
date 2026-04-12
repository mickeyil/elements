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

    // Run due internal copy operations assigned to a layer boundary.
    void run_copy_ops_for_stage(float t_rel, uint8_t before_layer_idx);

    // Copy logical pixels from src to dst. Decoder validation should ensure
    // equal sizes; this helper still checks defensively in the draft.
    static void copy_view(const PixelView& src, PixelView& dst);

    // Owned decoded/runtime program.
    Program* _program = nullptr;

    // Internal final-frame compositor.
    Compositor _compositor;

    // Owned per-layer playback progression state.
    LayerPlaybackState* _layer_states = nullptr;

    // Reused per-frame compositor input. One entry per layer, in layer order.
    PixelView** _active_dst_views = nullptr;

    // Per-copy execution flags. The simple draft implementation scans the
    // copy-op table by stage; final code can replace this with per-stage
    // cursors if copy-op count becomes meaningful.
    bool* _copy_done = nullptr;
};
