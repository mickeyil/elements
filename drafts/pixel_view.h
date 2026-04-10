#pragma once

// Draft-only API.
//
// PixelView is a logical view over a real hsva_t backing buffer.
//
// Ownership model:
// - backing hsva_t buffer: non-owning, provided by PixelBufferPool
// - optional index indirection array: owned by PixelView

#include <cstdint>

#include "colors.h"

class PixelView {
public:
    PixelView() = default;
    ~PixelView();

    PixelView(const PixelView&) = delete;
    PixelView& operator=(const PixelView&) = delete;
    PixelView(PixelView&&) = delete;
    PixelView& operator=(PixelView&&) = delete;

    // Initialize the view from a backing buffer and optional indirection list.
    //
    // If indices == nullptr, the view is identity-mapped and owns no index
    // storage. Otherwise the view copies the incoming index list and owns it.
    bool initialize(hsva_t* backing_buffer, uint8_t size, const uint16_t* indices = nullptr);

    // Release owned indirection state and clear the binding.
    void reset();

    // Number of logical pixels exposed by the view.
    uint8_t size() const;

    // True when the view maps directly onto the first `size()` backing pixels.
    bool is_identity() const;

    bool empty() const;

    // Logical pixel access.
    hsva_t& operator[](uint8_t i);
    const hsva_t& operator[](uint8_t i) const;

    // Zero every logical pixel reachable through the view.
    void clear();

private:
    // Non-owning pointer to the real hsva_t buffer.
    hsva_t* _buffer = nullptr;

    // Optional owned logical->physical slot mapping.
    // nullptr means identity mapping.
    uint16_t* _indices = nullptr;

    // Number of logical pixels exposed by this view.
    uint8_t _size = 0;
};
