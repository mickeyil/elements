#pragma once

// Draft-only API.
//
// CopyOps is an internal preservation timeline emitted by the compiler.
//
// A copy op is not a visual layer and is never composited. It exists to make
// source preservation explicit: at a scheduled time, copy the logical pixels
// from one PixelView into another PixelView. The compiler is responsible for
// scheduling copy ops before any dependent animation initializes.

#include <cstdint>

#include "runtime_constants.h"

struct CopyOp {
    // Program-relative time in seconds when this copy becomes due.
    float at = 0.0f;

    // Layer stage where the copy should run.
    //
    // 0 means before visual layer 0.
    // N means before visual layer N.
    // layer_count means after all visual layers.
    //
    // This lets the compiler preserve same-frame source output from a lower
    // layer before a higher dependent layer initializes.
    uint8_t before_layer_idx = 0;

    // Source view to copy from. This view may use storage indirection to read
    // a subset/reordered logical view over preserved storage.
    uint16_t src_pixv_idx = PIXV_NONE;

    // Destination view to copy into. This is usually a work/preserved buffer
    // view and normally does not need physical output mapping.
    uint16_t dst_pixv_idx = PIXV_NONE;
};

class CopyOps {
public:
    CopyOps() = default;
    ~CopyOps();

    CopyOps(const CopyOps&) = delete;
    CopyOps& operator=(const CopyOps&) = delete;
    CopyOps(CopyOps&&) = delete;
    CopyOps& operator=(CopyOps&&) = delete;

    // Copy the ordered decoder-provided copy-op table.
    //
    // The decoder/compiler should validate that ops reference valid PixelViews,
    // have equal src/dst sizes, and use a valid before_layer_idx. Keeping the
    // blob sorted by (at, before_layer_idx) is recommended for a later cursor
    // optimization, but this draft runtime does not rely on it.
    bool initialize(const CopyOp* ops, uint16_t count);

    void reset();

    uint16_t count() const;

    CopyOp& at(uint16_t idx);
    const CopyOp& at(uint16_t idx) const;

private:
    // Number of scheduled internal copy operations.
    uint16_t _count = 0;

    // Owned ordered copy-op table.
    CopyOp* _ops = nullptr;
};
