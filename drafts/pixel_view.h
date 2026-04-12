#pragma once

// Draft-only API.
//
// PixelView is a logical view over a real hsva_t backing buffer.
//
// Ownership model:
// - backing hsva_t buffer: non-owning, provided by PixelBufferPool
// - optional storage index indirection array: owned by PixelView
// - optional physical output mapping array: owned by PixelView
//
// The two mappings answer different questions:
// - storage mapping: where view[i] reads/writes in memory
// - physical mapping: where view[i] appears on the output strip
//
// Animations only use operator[] and do not know about physical LEDs.
// The compositor only receives active dst views and uses physical_index().

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

    // Initialize the view from a backing buffer and optional mappings.
    //
    // If storage_indices == nullptr, storage is identity-mapped:
    //   view[i] -> backing_buffer[i]
    //
    // If has_physical_mapping is false, this view is storage-only and must not
    // be passed to the compositor.
    //
    // If has_physical_mapping is true and physical_identity is true:
    //   physical_index(i) -> i
    //
    // If has_physical_mapping is true and physical_identity is false,
    // physical_indices must point to size uint16_t entries that are copied and
    // owned by the view.
    bool initialize(
        hsva_t* backing_buffer,
        uint16_t size,
        const uint16_t* storage_indices = nullptr,
        bool has_physical_mapping = false,
        const uint16_t* physical_indices = nullptr,
        bool physical_identity = false
    );

    // Release owned indirection state and clear the binding.
    void reset();

    // Number of logical pixels exposed by the view.
    uint16_t size() const;

    // True when storage maps directly onto backing pixels [0..size).
    bool is_storage_identity() const;

    // True when the view carries physical placement for compositor output.
    bool has_physical_mapping() const;

    // True when physical placement maps logical pixel i to physical LED i.
    bool is_physical_identity() const;

    bool empty() const;

    // Logical pixel access.
    hsva_t& operator[](uint16_t i);
    const hsva_t& operator[](uint16_t i) const;

    // Physical LED index for logical pixel i. Caller must only use this when
    // has_physical_mapping() is true.
    uint16_t physical_index(uint16_t i) const;

    // Zero every logical pixel reachable through the view.
    void clear();

private:
    // Non-owning pointer to the real hsva_t buffer.
    hsva_t* _buffer = nullptr;

    // Optional owned logical->storage-slot mapping.
    // nullptr means identity storage mapping.
    uint16_t* _storage_indices = nullptr;

    // Optional owned logical->physical-LED mapping.
    // nullptr means either no physical mapping or identity physical mapping,
    // depending on the flags below.
    uint16_t* _physical_indices = nullptr;

    // Number of logical pixels exposed by this view.
    uint16_t _size = 0;

    bool _has_physical_mapping = false;
    bool _physical_identity = false;
};
