#pragma once

#include <cstdint>

#include "core/compositor.h"
#include "core/program.h"
#include "core/strip.h"

// Engine plays a decoded Program. It tracks where each layer is on the
// timeline, runs due copy ops, lets each active animation render its dst
// view, and composites the result into a Strip.
//
// Cross-layer data flow is trusted, not re-validated: the compiler
// guarantees a copy-op source sits on a layer <= its dependent's and its
// event ends before the dependent starts.
//
// Engine owns the Program for its whole lifetime. Build with
// Engine::create(); free by deleting the Engine.

class Engine {
public:
    // Build an Engine for `program`. Engine takes ownership whether or not
    // the call succeeds: on failure the Program is freed via free_program()
    // before nullptr is returned. Returns nullptr if Engine itself or any of
    // its internal allocations fail.
    static Engine* create(Program* program);

    ~Engine();

    // Render the frame at `t_program` into `out`. Returns false once
    // `t_program` reaches the program duration.
    bool render_frame(ProgramTime t_program, Strip& out);

    // Rewind every layer to its first event, clear per-layer init flags,
    // rewind the copy-op cursor, and zero every pool buffer.
    void reset();

private:
    struct LayerPlaybackState {
        uint16_t cursor = 0;
        bool initialized = false;
    };

    explicit Engine(Program* program);

    // Allocate per-layer state and the active-view table. Returns false on
    // OOM; create() then deletes the Engine.
    bool initialize_();

    // Run every copy op with at <= t_program that hasn't run yet.
    void run_copy_ops_until(ProgramTime t_program);

    static void copy_view(const PixelView& src, PixelView& dst);

    Program* _program = nullptr;
    Compositor _compositor;
    LayerPlaybackState* _layer_states = nullptr;
    PixelView** _active_dst_views = nullptr;
    uint16_t _copy_cursor = 0;
};
