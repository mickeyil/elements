#pragma once

#include <cstdint>

#include "pixel_buffer_pool.h"
#include "pixel_view.h"
#include "runtime_constants.h"

// PixelViews is a table of PixelView records, indexed by view index.
// It is built once from a list of PixelViewSpec records -- each spec
// says which pool buffer a view binds to and which storage and
// physical mappings it uses.
//
// PixelViews owns the underlying array; each view in turn owns its
// own indirection arrays. Bind via initialize(); release via reset()
// or destruction.
//
//     PixelViews views;
//     views.initialize(pool, specs, n);  // builds n PixelViews from specs
//     PixelView& v = views.at(2);

// Input record describing one PixelView to build. Each field maps to
// a PixelView::initialize argument; see pixel_view.h for the storage
// and physical mapping semantics.
//
// Index arrays are trusted (the decoder bounds-checks their entries).
// PixelViews only checks that flags agree with their index arrays.
struct PixelViewSpec {
    uint16_t buffer_idx = PIXBUF_NONE;
    uint16_t size = 0;

    bool storage_identity = true;
    const uint16_t* storage_indices = nullptr;

    bool has_physical_mapping = false;
    bool physical_identity = false;
    const uint16_t* physical_indices = nullptr;
};

class PixelViews {
public:
    PixelViews() = default;
    ~PixelViews();

    PixelViews(const PixelViews&) = delete;
    PixelViews& operator=(const PixelViews&) = delete;
    PixelViews(PixelViews&&) = delete;
    PixelViews& operator=(PixelViews&&) = delete;

    // Build the table from `count` specs. The pool resolves each spec's
    // buffer_idx to a real hsva_t buffer. Returns false on validation
    // failure or allocation failure (the table is left empty).
    bool initialize(PixelBufferPool& buffers, const PixelViewSpec* specs, uint16_t count);

    // Release all owned views.
    void reset();

    uint16_t count() const { return _count; }

    // Look up a view by index. No bounds check; caller must use idx < count().
    PixelView& at(uint16_t idx) { return _views[idx]; }
    const PixelView& at(uint16_t idx) const { return _views[idx]; }

private:
    uint16_t _count = 0;
    PixelView* _views = nullptr;
};
