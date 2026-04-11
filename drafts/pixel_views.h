#pragma once

// Draft-only API.
//
// PixelViews owns the runtime PixelView[] table built by the decoder from
// temporary PixelViewSpec metadata.

#include <cstdint>

#include "pixel_buffer_pool.h"
#include "pixel_view.h"
#include "runtime_constants.h"

struct PixelViewSpec {
    // Which real PixelBufferPool buffer this view is built on top of.
    // This is decoder input metadata, not a runtime animation-facing field.
    uint16_t buffer_idx = PIXBUF_NONE;

    // Number of logical pixels exposed through the view.
    uint16_t size = 0;

    // True when the view maps directly onto backing buffer slots [0..size).
    bool is_identity = true;

    // Decoder input logical->buffer-slot mapping.
    // Null when `is_identity == true`.
    const uint16_t* indices = nullptr;
};

class PixelViews {
public:
    PixelViews() = default;
    ~PixelViews();

    PixelViews(const PixelViews&) = delete;
    PixelViews& operator=(const PixelViews&) = delete;
    PixelViews(PixelViews&&) = delete;
    PixelViews& operator=(PixelViews&&) = delete;

    // Build the runtime PixelView table from decoder input specs.
    //
    // PixelViews owns the resulting PixelView[] array. Each PixelView in turn
    // owns its copied index list, if any.
    bool initialize(PixelBufferPool& buffers, const PixelViewSpec* specs, uint16_t count);

    // Release every runtime PixelView and its owned metadata.
    void reset();

    uint16_t count() const;

    PixelView& at(uint16_t idx);
    const PixelView& at(uint16_t idx) const;

private:
    // Number of resolved runtime PixelViews.
    uint16_t _count = 0;

    // Owned array of runtime PixelViews.
    PixelView* _views = nullptr;
};
