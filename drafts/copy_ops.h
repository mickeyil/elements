#pragma once

// Draft-only API.
//
// CopyOps is an internal preservation timeline emitted by the compiler.
//
// A copy op is not a visual layer and is never composited. It exists to make
// source preservation explicit: at a scheduled time, copy already-rendered
// logical pixels from one PixelView into another PixelView. It preserves
// existing rendered state; it does not evaluate an animation.

#include <cstdint>

#include "runtime_constants.h"

struct CopyOp {
    // Program-relative time in seconds when this copy becomes due.
    float at = 0.0f;

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
    // have equal src/dst sizes, and are sorted by `at`.
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
