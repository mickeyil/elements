#include <catch2/catch_test_macros.hpp>

#include <cstdint>

#include "../src/pixel_buffer_pool.h"

// ---------------------------------------------------------------------------
// Empty / invalid input
// ---------------------------------------------------------------------------

TEST_CASE("PixelBufferPool: default-constructed is empty", "[pixel_buffer_pool]") {
    PixelBufferPool pool;
    CHECK(pool.buffer_count() == 0);
    CHECK(pool.buffer_at(0) == nullptr);
    CHECK(pool.buffer_size(0) == 0);
}

TEST_CASE("PixelBufferPool: initialize with zero buffers succeeds", "[pixel_buffer_pool]") {
    PixelBufferPool pool;
    REQUIRE(pool.initialize(nullptr, 0));
    CHECK(pool.buffer_count() == 0);
}

TEST_CASE("PixelBufferPool: null sizes with non-zero count fails", "[pixel_buffer_pool]") {
    PixelBufferPool pool;
    CHECK_FALSE(pool.initialize(nullptr, 3));
    CHECK(pool.buffer_count() == 0);
}

// ---------------------------------------------------------------------------
// Allocation
// ---------------------------------------------------------------------------

TEST_CASE("PixelBufferPool: allocates buffers of requested sizes", "[pixel_buffer_pool]") {
    PixelBufferPool pool;
    uint16_t sizes[] = {32, 64, 16};
    REQUIRE(pool.initialize(sizes, 3));

    REQUIRE(pool.buffer_count() == 3);
    CHECK(pool.buffer_size(0) == 32);
    CHECK(pool.buffer_size(1) == 64);
    CHECK(pool.buffer_size(2) == 16);

    CHECK(pool.buffer_at(0) != nullptr);
    CHECK(pool.buffer_at(1) != nullptr);
    CHECK(pool.buffer_at(2) != nullptr);
}

TEST_CASE("PixelBufferPool: zero-size buffer in the list is allowed", "[pixel_buffer_pool]") {
    PixelBufferPool pool;
    uint16_t sizes[] = {16, 0, 16};
    REQUIRE(pool.initialize(sizes, 3));

    CHECK(pool.buffer_size(0) == 16);
    CHECK(pool.buffer_size(1) == 0);
    CHECK(pool.buffer_size(2) == 16);

    // The two non-empty buffers must be reachable.
    CHECK(pool.buffer_at(0) != nullptr);
    CHECK(pool.buffer_at(2) != nullptr);
}

// ---------------------------------------------------------------------------
// Out-of-range access
// ---------------------------------------------------------------------------

TEST_CASE("PixelBufferPool: out-of-range buffer_at returns nullptr", "[pixel_buffer_pool]") {
    PixelBufferPool pool;
    uint16_t sizes[] = {8, 8};
    REQUIRE(pool.initialize(sizes, 2));

    CHECK(pool.buffer_at(2) == nullptr);
    CHECK(pool.buffer_at(0xFFFF) == nullptr);  // PIXBUF_NONE
    CHECK(pool.buffer_size(2) == 0);
    CHECK(pool.buffer_size(0xFFFF) == 0);
}

// ---------------------------------------------------------------------------
// Per-buffer isolation
// ---------------------------------------------------------------------------

TEST_CASE("PixelBufferPool: writes to one buffer don't bleed into others",
          "[pixel_buffer_pool]") {
    PixelBufferPool pool;
    uint16_t sizes[] = {4, 4, 4};
    REQUIRE(pool.initialize(sizes, 3));

    hsva_t* buf0 = pool.buffer_at(0);
    hsva_t* buf1 = pool.buffer_at(1);
    hsva_t* buf2 = pool.buffer_at(2);
    REQUIRE(buf0 != nullptr);
    REQUIRE(buf1 != nullptr);
    REQUIRE(buf2 != nullptr);

    // Fill buffer 1 with markers.
    for (uint16_t i = 0; i < 4; i++) {
        buf1[i] = hsva_t(123.0f, 0.5f, 0.7f, 1.0f);
    }

    // Buffers 0 and 2 should still be zero-initialized.
    for (uint16_t i = 0; i < 4; i++) {
        CHECK(buf0[i].h == 0.0f);
        CHECK(buf0[i].s == 0.0f);
        CHECK(buf2[i].h == 0.0f);
        CHECK(buf2[i].s == 0.0f);
    }

    // Buffer 1 retains its writes.
    for (uint16_t i = 0; i < 4; i++) {
        CHECK(buf1[i].h == 123.0f);
        CHECK(buf1[i].s == 0.5f);
    }
}

// ---------------------------------------------------------------------------
// Reset / re-initialize
// ---------------------------------------------------------------------------

TEST_CASE("PixelBufferPool: reset returns to empty state", "[pixel_buffer_pool]") {
    PixelBufferPool pool;
    uint16_t sizes[] = {16, 32};
    REQUIRE(pool.initialize(sizes, 2));

    pool.reset();

    CHECK(pool.buffer_count() == 0);
    CHECK(pool.buffer_at(0) == nullptr);
    CHECK(pool.buffer_size(0) == 0);
}

TEST_CASE("PixelBufferPool: re-initialize replaces previous allocation",
          "[pixel_buffer_pool]") {
    PixelBufferPool pool;
    uint16_t first[] = {16, 16};
    REQUIRE(pool.initialize(first, 2));

    uint16_t second[] = {8, 8, 8, 8};
    REQUIRE(pool.initialize(second, 4));

    CHECK(pool.buffer_count() == 4);
    CHECK(pool.buffer_size(0) == 8);
    CHECK(pool.buffer_size(3) == 8);
    CHECK(pool.buffer_at(0) != nullptr);
    CHECK(pool.buffer_at(3) != nullptr);
}

// ---------------------------------------------------------------------------
// Pooled-mode adjacency invariant (ARDUINO build only)
// ---------------------------------------------------------------------------

#ifdef ARDUINO
TEST_CASE("PixelBufferPool (pooled): logical buffers are contiguous",
          "[pixel_buffer_pool][pooled]") {
    PixelBufferPool pool;
    uint16_t sizes[] = {4, 8, 2, 16};
    REQUIRE(pool.initialize(sizes, 4));

    CHECK(pool.buffer_at(1) == pool.buffer_at(0) + sizes[0]);
    CHECK(pool.buffer_at(2) == pool.buffer_at(1) + sizes[1]);
    CHECK(pool.buffer_at(3) == pool.buffer_at(2) + sizes[2]);
}
#endif
