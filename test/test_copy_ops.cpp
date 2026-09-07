#include <catch2/catch_test_macros.hpp>

#include <cstdint>

#include "core/copy_ops.h"
#include "core/runtime_constants.h"

namespace {
CopyOp make_op(uint32_t at_ms, uint16_t src, uint16_t dst) {
    CopyOp op;
    op.at = ProgramTime{at_ms};
    op.src_pixv_idx = src;
    op.dst_pixv_idx = dst;
    return op;
}
}  // namespace

// ---------------------------------------------------------------------------
// Empty / invalid input
// ---------------------------------------------------------------------------

TEST_CASE("CopyOps: default-constructed is empty", "[copy_ops]") {
    CopyOps ops;
    CHECK(ops.count() == 0);
}

TEST_CASE("CopyOps: initialize with zero count succeeds", "[copy_ops]") {
    CopyOps ops;
    REQUIRE(ops.initialize(nullptr, 0));
    CHECK(ops.count() == 0);
}

TEST_CASE("CopyOps: null ops with non-zero count fails", "[copy_ops]") {
    CopyOps ops;
    CHECK_FALSE(ops.initialize(nullptr, 3));
    CHECK(ops.count() == 0);
}

// ---------------------------------------------------------------------------
// Construction and access
// ---------------------------------------------------------------------------

TEST_CASE("CopyOps: copies records and reports count", "[copy_ops]") {
    const CopyOp input[] = {
        make_op(500, 1, 2),
        make_op(1000, 3, 4),
    };
    CopyOps ops;
    REQUIRE(ops.initialize(input, 2));

    REQUIRE(ops.count() == 2);
    CHECK(ops.at(0).at.ms == 500);
    CHECK(ops.at(0).src_pixv_idx == 1);
    CHECK(ops.at(0).dst_pixv_idx == 2);
    CHECK(ops.at(1).at.ms == 1000);
    CHECK(ops.at(1).src_pixv_idx == 3);
    CHECK(ops.at(1).dst_pixv_idx == 4);
}

TEST_CASE("CopyOps: input array is copied (caller may free)", "[copy_ops]") {
    CopyOp* input = new CopyOp[2]{
        make_op(100, 5, 6),
        make_op(200, 7, 8),
    };
    CopyOps ops;
    REQUIRE(ops.initialize(input, 2));

    delete[] input;  // ops must not depend on this

    CHECK(ops.at(0).src_pixv_idx == 5);
    CHECK(ops.at(1).at.ms == 200);
}

// ---------------------------------------------------------------------------
// Order preservation
// ---------------------------------------------------------------------------

TEST_CASE("CopyOps: preserves the input table order", "[copy_ops]") {
    const CopyOp input[] = {
        make_op(0, 0, 1),
        make_op(500, 1, 2),
        make_op(1500, 2, 3),
        make_op(2000, 3, 4),
    };
    CopyOps ops;
    REQUIRE(ops.initialize(input, 4));

    REQUIRE(ops.count() == 4);
    for (uint16_t i = 0; i < 4; i++) {
        CHECK(ops.at(i).at == input[i].at);
        CHECK(ops.at(i).src_pixv_idx == input[i].src_pixv_idx);
        CHECK(ops.at(i).dst_pixv_idx == input[i].dst_pixv_idx);
    }
}

TEST_CASE("CopyOps: same-`at` ops keep their input order", "[copy_ops]") {
    // Two ops at the same time; CopyOps must preserve their relative order
    // because same-`at` execution order is significant.
    const CopyOp input[] = {
        make_op(1000, 10, 20),  // first same-at writer
        make_op(1000, 11, 21),  // second same-at writer
        make_op(2000, 12, 22),
    };
    CopyOps ops;
    REQUIRE(ops.initialize(input, 3));

    CHECK(ops.at(0).src_pixv_idx == 10);
    CHECK(ops.at(0).dst_pixv_idx == 20);
    CHECK(ops.at(1).src_pixv_idx == 11);
    CHECK(ops.at(1).dst_pixv_idx == 21);
}

// ---------------------------------------------------------------------------
// reset / re-initialize
// ---------------------------------------------------------------------------

TEST_CASE("CopyOps: reset returns to empty state", "[copy_ops]") {
    const CopyOp input[] = { make_op(500, 1, 2) };
    CopyOps ops;
    REQUIRE(ops.initialize(input, 1));

    ops.reset();

    CHECK(ops.count() == 0);
}

TEST_CASE("CopyOps: re-initialize replaces previous table", "[copy_ops]") {
    const CopyOp first[] = { make_op(500, 1, 2) };
    const CopyOp second[] = {
        make_op(0, 9, 10),
        make_op(100, 11, 12),
    };
    CopyOps ops;
    REQUIRE(ops.initialize(first, 1));

    REQUIRE(ops.initialize(second, 2));

    REQUIRE(ops.count() == 2);
    CHECK(ops.at(0).src_pixv_idx == 9);
    CHECK(ops.at(1).dst_pixv_idx == 12);
}
