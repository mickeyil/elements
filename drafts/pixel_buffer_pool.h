#pragma once

// Draft-only API.
//
// PixelBufferPool owns the real hsva_t backing buffers used by a decoded
// program. Logical PixelViews are later bound onto these buffers.
//
// Draft note:
// - On ARDUINO builds, the intended implementation is a compact pooled
//   allocation to minimize fragmentation on ESP32.
// - On non-ARDUINO builds, the intended implementation is one allocation per
//   logical buffer so tools such as Valgrind remain effective at catching
//   inter-buffer out-of-bounds writes.
//
// Desired tests for this class:
// 1. Host unit tests for the non-ARDUINO separate-allocation path.
//    - verify initialize/reset/buffer_at/buffer_size behavior
//    - run under Valgrind
// 2. Host unit tests for the ARDUINO pooled path.
//    - compile the same class with ARDUINO defined on the test target
//    - verify contiguous pooled layout and the same public behavior
// 3. Behavioral equivalence tests across both implementations.
//    - same logical buffer inputs
//    - same externally visible results
// 4. Pooled-layout invariant tests.
//    - adjacency of pooled slices
//    - correct size reporting
//    - hsva_t-aligned resolved pointers
//    - correct teardown/reset behavior

#include <cstdint>

#include "colors.h"

class PixelBufferPool {
public:
    PixelBufferPool() = default;
    ~PixelBufferPool();

    PixelBufferPool(const PixelBufferPool&) = delete;
    PixelBufferPool& operator=(const PixelBufferPool&) = delete;
    PixelBufferPool(PixelBufferPool&&) = delete;
    PixelBufferPool& operator=(PixelBufferPool&&) = delete;

    // Allocate N logical buffers using an ordered list of pixel counts.
    //
    // The intended implementation is one contiguous hsva_t allocation plus an
    // array of resolved buffer pointers. Callers still address buffers by
    // index rather than by offset.
    bool initialize(const uint8_t* buffer_sizes, uint16_t buffer_count);

    // Release all owned storage.
    void reset();

    // Number of logical buffers owned by the pool.
    uint16_t buffer_count() const;

    // Start pointer of logical buffer `buffer_idx`.
    hsva_t* buffer_at(uint16_t buffer_idx);
    const hsva_t* buffer_at(uint16_t buffer_idx) const;

    // Pixel length of logical buffer `buffer_idx`.
    uint8_t buffer_size(uint16_t buffer_idx) const;

private:
#ifdef ARDUINO
    // Contiguous owned storage for every hsva_t buffer in the decoded program.
    hsva_t* _pool = nullptr;

    // Total hsva_t count stored in `_pool`.
    uint32_t _pool_size = 0;
#endif

    // Number of logical buffers owned by the pool.
    uint16_t _buffer_count = 0;

    // Resolved start pointer for logical buffer i.
    hsva_t** _buffers = nullptr;

    // Pixel length of logical buffer i.
    uint8_t* _sizes = nullptr;
};
