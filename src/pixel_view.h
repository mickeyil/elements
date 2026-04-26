#pragma once

#include <cstdint>

#include "colors.h"

// PixelView is a logical view over an hsva_t backing buffer, exposing
// `size` logical pixels through two independent mappings:
//
// - storage: where view[i] reads/writes in the buffer (identity by
//   default; an optional indirection array can remap)
// - physical: where view[i] appears on the strip (absent by default;
//   set only on views the compositor consumes)
//
// The view borrows its backing buffer (PixelBufferPool owns it) but
// owns its indirection arrays. Bind via initialize(); release via
// reset() or destruction.
//
//     PixelView v;
//     v.initialize(backing, /*size*/ 8);
//     v[3] = hsva_t(120.0f, 1.0f, 0.5f);

class PixelView {
public:
    PixelView() = default;
    ~PixelView();

    PixelView(const PixelView&) = delete;
    PixelView& operator=(const PixelView&) = delete;
    PixelView(PixelView&&) = delete;
    PixelView& operator=(PixelView&&) = delete;

    // Bind the view to a backing buffer with optional indirection arrays.
    //
    // storage_indices == nullptr: identity storage (view[i] -> backing[i]).
    // Otherwise the array is copied and view[i] -> backing[storage_indices[i]].
    //
    // has_physical_mapping == false: storage-only view; must not be passed
    // to the compositor. The physical_* arguments are ignored.
    // has_physical_mapping == true && physical_identity == true:
    //     physical_index(i) == i.
    // has_physical_mapping == true && physical_identity == false:
    //     physical_indices must point to `size` entries (copied and owned).
    //
    // Returns false on allocation failure or invalid arguments (size > 0
    // with backing_buffer == nullptr, or non-identity physical mapping
    // with physical_indices == nullptr).
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

    // Zero every logical pixel reachable through the view.
    void clear();

    uint16_t size() const { return _size; }

    bool empty() const { return _size == 0; }

    // True when storage is identity (view[i] == backing[i]).
    bool is_storage_identity() const { return _storage_indices == nullptr; }

    // True when the view carries a physical-LED mapping.
    bool has_physical_mapping() const { return _has_physical_mapping; }

    // True when physical mapping is identity (physical_index(i) == i).
    bool is_physical_identity() const {
        return _has_physical_mapping && _physical_identity;
    }

    // Logical pixel access.
    hsva_t& operator[](uint16_t i) {
        return _buffer[_storage_indices ? _storage_indices[i] : i];
    }
    const hsva_t& operator[](uint16_t i) const {
        return _buffer[_storage_indices ? _storage_indices[i] : i];
    }

    // Physical LED index for logical pixel i. Only meaningful when
    // has_physical_mapping() is true; returns 0 otherwise.
    uint16_t physical_index(uint16_t i) const {
        if (!_has_physical_mapping) return 0;
        return _physical_identity ? i : _physical_indices[i];
    }

private:
    // Non-owning pointer to the backing hsva_t buffer.
    hsva_t* _buffer = nullptr;

    // Owned logical -> storage-slot mapping. nullptr means identity.
    uint16_t* _storage_indices = nullptr;

    // Owned logical -> physical-LED mapping. nullptr means either no
    // physical mapping or identity physical mapping; the flags below
    // distinguish.
    uint16_t* _physical_indices = nullptr;

    uint16_t _size = 0;
    bool _has_physical_mapping = false;
    bool _physical_identity = false;
};
