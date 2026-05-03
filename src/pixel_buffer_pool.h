#pragma once

#include <cstdint>

#include "colors.h"

// PixelBufferPool is a collection of hsva_t pixel buffers used by a
// decoded program. Buffers are addressed by index. The pool owns the
// memory: it is allocated on initialize() and released on reset() or
// destruction. Callers borrow buffer pointers via buffer_at() and must
// not free them.
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
    ~PixelBufferPool();

    // Allocate buffer_count logical buffers from the given pixel-count list.
    // Returns false on allocation failure (the pool is left empty).
    bool initialize(const uint16_t* buffer_sizes, uint16_t buffer_count);

    // Release all owned storage.
    void reset();

    uint16_t buffer_count() const { return _buffer_count; }

    // Start pointer of logical buffer `buffer_idx`. Returns nullptr if
    // out of range.
    hsva_t* buffer_at(uint16_t buffer_idx) {
        if (buffer_idx >= _buffer_count || _buffers == nullptr) {
            return nullptr;
        }
        return _buffers[buffer_idx];
    }
    const hsva_t* buffer_at(uint16_t buffer_idx) const {
        if (buffer_idx >= _buffer_count || _buffers == nullptr) {
            return nullptr;
        }
        return _buffers[buffer_idx];
    }

    // Pixel length of logical buffer `buffer_idx`. Returns 0 if out of
    // range.
    uint16_t buffer_size(uint16_t buffer_idx) const {
        if (buffer_idx >= _buffer_count || _sizes == nullptr) {
            return 0;
        }
        return _sizes[buffer_idx];
    }

private:
    // ARDUINO: _pool is one contiguous allocation, _buffers[i] points
    // into it. Host: each _buffers[i] is its own allocation; _pool is
    // absent.
#ifdef ARDUINO
    hsva_t* _pool = nullptr;
#endif

    uint16_t _buffer_count = 0;
    hsva_t** _buffers = nullptr;
    uint16_t* _sizes = nullptr;
};
