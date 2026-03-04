#pragma once

#include "decoder.h"
#include "compositor.h"
#include "animation.h"

class Engine {
public:
    // Takes ownership of prog (freed on destroy).
    Engine(Program* prog, Strip& strip);
    ~Engine();

    // Advance to time t (seconds since program start).
    // Returns false if program has ended.
    bool tick(float t);

private:
    struct LayerState {
        uint16_t cursor;
        Animation* instance;
    };

    Program* _prog;
    Compositor _compositor;
    LayerState* _states;
};
