#pragma once

#include <cstdint>

#include "copy_ops.h"
#include "layer.h"
#include "pixel_buffer_pool.h"
#include "pixel_views.h"

// Program holds a decoded blob in memory: pixel storage, views, copy ops,
// and the layer timeline. The decoder builds one and hands it to Engine,
// which keeps it until shutdown. Free with free_program().

struct Program {
    ~Program();

    // How long the program plays, in seconds.
    float duration = 0.0f;

    // Frames per second the program was authored for. The runtime uses this
    // to pace playback.
    uint8_t target_fps = 50;

    // True when the program must play in sync with other devices.
    bool requires_sync = false;

    uint8_t layer_count = 0;

    // The bottom-to-top layer timeline. Each layer owns its own events and
    // animations; ~Program() frees the array.
    Layer* layers = nullptr;

    PixelBufferPool pixel_buffer_pool;
    PixelViews pixel_views;
    CopyOps copy_ops;
};

// Free a Program. Safe with nullptr. Same as `delete prog`: every owned
// table and animation gets freed.
void free_program(Program* prog);
