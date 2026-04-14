#pragma once

// Draft-only API.
//
// Proposed decoded-program structures for the PixelBufferPool + PixelView
// redesign. These are intentionally isolated from the current runtime code so
// the design can be refined without touching build-integrated headers.

#include <cstdint>

#include "copy_ops.h"
#include "layer.h"
#include "pixel_buffer_pool.h"
#include "pixel_views.h"

struct Program {
    Program() = default;
    ~Program();

    Program(const Program&) = delete;
    Program& operator=(const Program&) = delete;
    Program(Program&&) = delete;
    Program& operator=(Program&&) = delete;

    // Total program duration in seconds. Must be finite and > 0.
    float duration = 0.0f;

    // True when the artifact must run against controller-synchronized time.
    // Read once at handle_load() into Playback::_requires_sync.
    bool requires_sync = false;

    // Number of visual layer timelines.
    uint8_t layer_count = 0;

    // Bottom-to-top visual layer order. Owned; deleted by ~Program(). Each
    // Layer's destructor releases its own events array and animations.
    Layer* layers = nullptr;

    // Owns all real hsva_t backing buffers.
    PixelBufferPool pixel_buffer_pool;

    // Owns the runtime PixelView table.
    PixelViews pixel_views;

    // Owns ordered internal source-preservation copy operations.
    //
    // Copy ops run before visual rendering once they become due. They are not
    // layers and are not passed to the compositor.
    CopyOps copy_ops;
};

// Free a Program returned by decode_program(). Safe to call with nullptr.
//
// Thin wrapper around `delete prog;` — Program::~Program() releases the
// layers array, each Layer destructor releases its events and animations,
// and the embedded containers (PixelBufferPool, PixelViews, CopyOps) tear
// down via their own destructors.
void free_program(Program* prog);
