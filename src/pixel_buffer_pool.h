#pragma once

#include <cstdint>

#include "colors.h"

// Owns the hsva_t backing buffers used by a decoded program. PixelView
// instances bind onto these buffers and address them by index.
//
//     PixelBufferPool pool;
//     uint16_t sizes[] = {32, 64, 16};
//     pool.initialize(sizes, 3);
//     hsva_t* buf = pool.buffer_at(1);  // 64-pixel buffer
//
// Allocation differs by build target (contiguous on ARDUINO, per-buffer
// on host); public behavior is identical.

class PixelBufferPool {
public:
    PixelBufferPool() = default;
    ~PixelBufferPool();

    PixelBufferPool(const PixelBufferPool&) = delete;
    PixelBufferPool& operator=(const PixelBufferPool&) = delete;
    PixelBufferPool(PixelBufferPool&&) = delete;
    PixelBufferPool& operator=(PixelBufferPool&&) = delete;

    // Allocate buffer_count logical buffers from the given pixel-count list.
    // Returns false on allocation failure (the pool is left empty).
    bool initialize(const uint16_t* buffer_sizes, uint16_t buffer_count);

    // Release all owned storage.
    void reset();

    uint16_t buffer_count() const;

    // Start pointer of logical buffer `buffer_idx`. Returns nullptr if
    // out of range.
    hsva_t* buffer_at(uint16_t buffer_idx);
    const hsva_t* buffer_at(uint16_t buffer_idx) const;

    // Pixel length of logical buffer `buffer_idx`. Returns 0 if out of
    // range.
    uint16_t buffer_size(uint16_t buffer_idx) const;

private:
#ifdef ARDUINO
    hsva_t* _pool = nullptr;
    uint32_t _pool_size = 0;
#endif

    uint16_t _buffer_count = 0;
    hsva_t** _buffers = nullptr;
    uint16_t* _sizes = nullptr;
};
