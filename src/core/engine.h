#pragma once

#include <cstdint>

#include "core/compositor.h"
#include "core/program.h"
#include "core/strip.h"

// Engine plays a decoded Program. Each frame it first brings the timeline
// up to the requested time, one boundary at a time, then draws the events
// active at that time and composites them into a Strip. What a frame shows
// depends only on its time, never on which earlier frames were rendered.
//
// At each boundary time, in this order:
//   1. every event ending there renders once at its own duration: the
//      endpoint sample a later event picks up from its buffer
//   2. copy ops scheduled there run in table order
//   3. every event starting there initializes
// Only then does the frame draw. The compiler plans copy ops and buffer
// reuse around exactly this order.
//
// Cross-layer data flow is trusted, not re-validated: the compiler
// guarantees a copy-op source sits on a layer <= its dependent's and its
// event ends before the dependent starts.
//
// Engine owns the Program for its whole lifetime. Build with
// Engine::create(); free by deleting the Engine.

class Engine
{
public:
    // Build an Engine for `program`. Engine takes ownership whether or not
    // the call succeeds: on failure the Program is freed via free_program()
    // before nullptr is returned. Returns nullptr if Engine itself or any of
    // its internal allocations fail.
    static Engine* create(Program* program);

    ~Engine();

    // Render the frame at `t_program` into `out`. Frame times must not go
    // backwards between resets. Returns false once `t_program` reaches the
    // program duration.
    bool render_frame(ProgramTime t_program, Strip& out);

    // Rewind every layer to its first event, rewind the copy-op cursor, and
    // zero every pool buffer. The next frame replays the timeline from 0.
    void reset();

private:
    struct LayerPlaybackState
    {
        uint16_t cursor = 0;    // the event playing, or the next one to start
        bool started = false;   // has events[cursor] been initialized?
    };

    explicit Engine(Program* program);

    // Allocate per-layer state and the active-view table. Returns false on
    // OOM; create() then deletes the Engine.
    bool initialize_();

    // Apply every boundary at or before `t`, earliest first.
    void advance_to(ProgramTime t);

    // The earliest boundary not yet applied. False when none is left.
    bool next_boundary(ProgramTime& out) const;

    void finish_events_ending_at(ProgramTime t);
    void run_copy_ops_at(ProgramTime t);
    void start_events_starting_at(ProgramTime t);

    // Render every started event into its dst view and record the views
    // for the compositor.
    void render_active(ProgramTime t);

    static void copy_view(const PixelView& src, PixelView& dst);

    Program* _program = nullptr;
    Compositor _compositor;
    LayerPlaybackState* _layer_states = nullptr;
    PixelView** _active_dst_views = nullptr;
    uint16_t _copy_cursor = 0;
};
