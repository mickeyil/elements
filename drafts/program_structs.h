#pragma once

// Draft-only API.
//
// Proposed decoded-program structures for the PixelBufferPool + PixelView
// redesign. These are intentionally isolated from the current runtime code so
// the design can be refined without touching build-integrated headers.

#include <cstdint>

#include "layer.h"
#include "pixel_buffer_pool.h"
#include "pixel_views.h"

struct Program {
    // Total program duration in seconds.
    float duration = 0.0f;

    // Number of composited layers.
    uint8_t layer_count = 0;

    // Bottom-to-top compositing order.
    Layer* layers = nullptr;

    // Owns all real hsva_t backing buffers.
    PixelBufferPool pixel_buffer_pool;

    // Owns the runtime PixelView table.
    PixelViews pixel_views;
};

// Build the shared pixel runtime pieces of Program from decoder input.
//
// Final decoder flow is expected to:
// - initialize PixelBufferPool
// - initialize PixelViews
// - resolve canonical layer buffers from the pool
// - pass resolved buffers directly into Layer::initialize()
//
// No runtime layer buffer indices are retained in Program.
bool initialize_program_runtime(
    Program& prog,
    const uint16_t* buffer_sizes,
    uint16_t buffer_count,
    const PixelViewSpec* pixel_view_specs,
    uint16_t pixel_view_count
);

// Free all owned memory associated with Program.
void free_program_sketch(Program* prog);
