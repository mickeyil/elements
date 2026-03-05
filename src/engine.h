#pragma once

#include "decoder.h"
#include "compositor.h"
#include "animation.h"

class Engine {
public:
    // Takes ownership of prog (freed on destroy).
    Engine(Program* prog, Strip& strip, bool gamma_enabled);
    ~Engine();

    // Advance to time t (seconds since program start).
    // Returns false if program has ended.
    bool tick(float t);

    // Reset engine to initial state (cursors, instances, buffers).
    // After reset, tick(0) behaves as if the engine were freshly constructed.
    void reset();

private:
    struct LayerState {
        uint16_t cursor;
        Animation* instance;
    };

    Program* _prog;
    Compositor _compositor;
    LayerState* _states;
};
