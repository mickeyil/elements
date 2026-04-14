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
    // Fallible factory. Takes ownership of `program` on success AND on
    // failure: the caller hands off the Program pointer and never owns it
    // again. On failure the factory frees the Program via free_program()
    // before returning nullptr.
    //
    // Returns nullptr if Engine itself cannot be allocated, or if any of
    // its internal nothrow allocations (per-layer state, active-view
    // table) fails. Callers should log "engine alloc failed" and ACK the
    // load command with kAckError.
    static Engine* create(Program* program);

    ~Engine();

    Engine(const Engine&) = delete;
    Engine& operator=(const Engine&) = delete;
    Engine(Engine&&) = delete;
    Engine& operator=(Engine&&) = delete;

    // Advance to t_program and render the final RGB frame into `out`.
    //
    // Returns false when t_program is outside the playable program range.
    bool render_frame(float t_program, Strip& out);

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

    // Private constructor — store the Program pointer only, no allocations.
    // Use Engine::create() instead.
    explicit Engine(Program* program);

    // Allocate per-layer state and active-view table using nothrow new.
    // Returns false on OOM. Called by create() after construction.
    bool initialize_();

    // Run due internal copy operations before visual rendering for t_program.
    void run_copy_ops_until(float t_program);

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

    // Monotonic cursor into sorted Program::copy_ops.
    uint16_t _copy_cursor = 0;
};
