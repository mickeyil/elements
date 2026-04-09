#pragma once

// Draft-only API.
//
// PixelView is a non-owning logical view over a real hsva_t backing buffer.
// It may expose the buffer directly or through an index indirection table.

#include <cstdint>

#include "colors.h"

class PixelView {
public:
    PixelView() = default;

    // Identity view: logical pixel i maps directly to backing_buffer[i].
    PixelView(hsva_t* backing_buffer, uint8_t size);

    // Indexed view: logical pixel i maps to backing_buffer[indices[i]].
    PixelView(hsva_t* backing_buffer, const uint16_t* indices, uint8_t size);

    // Rebind this view to a new backing buffer and optional indirection array.
    void bind(hsva_t* backing_buffer, const uint16_t* indices, uint8_t size);

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

    // Optional logical->physical slot mapping. nullptr means identity mapping.
    const uint16_t* _indices = nullptr;

    // Number of logical pixels exposed by this view.
    uint8_t _size = 0;
};
