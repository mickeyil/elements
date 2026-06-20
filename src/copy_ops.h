#pragma once

#include <cstdint>

#include "runtime_constants.h"

// CopyOps is an ordered table of scheduled view-to-view copies. Each
// CopyOp says "at time `at`, copy view A into view B" -- this lets
// one animation pick up data prepared by another at the right
// moment.
//
// CopyOps owns the underlying array. Bind via initialize(); release
// via reset() or destruction. Order is significant: same-`at` ops
// execute in table order.

// The decoder validates records.
struct CopyOp {
    float at = 0.0f;  // program-relative seconds
    uint16_t src_pixv_idx = PIXV_NONE;
    uint16_t dst_pixv_idx = PIXV_NONE;
};

class CopyOps {
public:
    ~CopyOps();

    // Copy `count` records into the table. Returns false on allocation
    // failure (the table is left empty).
    bool initialize(const CopyOp* ops, uint16_t count);

    // Release the table.
    void reset();

    uint16_t count() const { return _count; }

    // Look up a record by index. No bounds check; caller must use idx < count().
    CopyOp& at(uint16_t idx) { return _ops[idx]; }
    const CopyOp& at(uint16_t idx) const { return _ops[idx]; }

private:
    uint16_t _count = 0;
    CopyOp* _ops = nullptr;
};
