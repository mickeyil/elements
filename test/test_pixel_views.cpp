#include <catch2/catch_test_macros.hpp>

#include <cstdint>

#include "../src/pixel_buffer_pool.h"
#include "../src/pixel_view.h"
#include "../src/pixel_views.h"
#include "../src/runtime_constants.h"

namespace {
hsva_t with_h(float h) { return hsva_t(h, 0.5f, 0.5f, 1.0f); }
}  // namespace

// ---------------------------------------------------------------------------
// Empty / invalid input
// ---------------------------------------------------------------------------

TEST_CASE("PixelViews: default-constructed is empty", "[pixel_views]") {
    PixelViews views;
    CHECK(views.count() == 0);
}

TEST_CASE("PixelViews: initialize with zero count succeeds", "[pixel_views]") {
    PixelBufferPool pool;
    PixelViews views;
    REQUIRE(views.initialize(pool, nullptr, 0));
    CHECK(views.count() == 0);
}

TEST_CASE("PixelViews: null specs with non-zero count fails", "[pixel_views]") {
    PixelBufferPool pool;
    PixelViews views;
    CHECK_FALSE(views.initialize(pool, nullptr, 3));
    CHECK(views.count() == 0);
}

// ---------------------------------------------------------------------------
// Valid construction
// ---------------------------------------------------------------------------

TEST_CASE("PixelViews: builds identity-storage views", "[pixel_views]") {
    PixelBufferPool pool;
    uint16_t buffer_sizes[] = {16, 32};
    REQUIRE(pool.initialize(buffer_sizes, 2));

    PixelViewSpec specs[2];
    specs[0].buffer_idx = 0;
    specs[0].size = 16;
    specs[0].storage_identity = true;
    specs[1].buffer_idx = 1;
    specs[1].size = 32;
    specs[1].storage_identity = true;

    PixelViews views;
    REQUIRE(views.initialize(pool, specs, 2));

    REQUIRE(views.count() == 2);
    CHECK(views.at(0).size() == 16);
    CHECK(views.at(0).is_storage_identity());
    CHECK(views.at(1).size() == 32);
    CHECK(views.at(1).is_storage_identity());
}

TEST_CASE("PixelViews: writes through the resulting view land in the pool buffer",
          "[pixel_views]") {
    PixelBufferPool pool;
    uint16_t buffer_sizes[] = {8};
    REQUIRE(pool.initialize(buffer_sizes, 1));

    PixelViewSpec spec;
    spec.buffer_idx = 0;
    spec.size = 8;
    spec.storage_identity = true;

    PixelViews views;
    REQUIRE(views.initialize(pool, &spec, 1));

    views.at(0)[3] = with_h(120.0f);
    CHECK(pool.buffer_at(0)[3].h == 120.0f);
}

TEST_CASE("PixelViews: non-identity storage routes writes through the indices",
          "[pixel_views]") {
    PixelBufferPool pool;
    uint16_t buffer_sizes[] = {8};
    REQUIRE(pool.initialize(buffer_sizes, 1));

    const uint16_t storage[] = {6, 4, 2};
    PixelViewSpec spec;
    spec.buffer_idx = 0;
    spec.size = 3;
    spec.storage_identity = false;
    spec.storage_indices = storage;

    PixelViews views;
    REQUIRE(views.initialize(pool, &spec, 1));

    CHECK_FALSE(views.at(0).is_storage_identity());
    views.at(0)[1] = with_h(120.0f);  // -> pool.buffer_at(0)[4]
    CHECK(pool.buffer_at(0)[4].h == 120.0f);
}

TEST_CASE("PixelViews: identity physical mapping is wired through", "[pixel_views]") {
    PixelBufferPool pool;
    uint16_t buffer_sizes[] = {4};
    REQUIRE(pool.initialize(buffer_sizes, 1));

    PixelViewSpec spec;
    spec.buffer_idx = 0;
    spec.size = 4;
    spec.storage_identity = true;
    spec.has_physical_mapping = true;
    spec.physical_identity = true;

    PixelViews views;
    REQUIRE(views.initialize(pool, &spec, 1));

    CHECK(views.at(0).has_physical_mapping());
    CHECK(views.at(0).is_physical_identity());
    for (uint16_t i = 0; i < 4; i++) {
        CHECK(views.at(0).physical_index(i) == i);
    }
}

TEST_CASE("PixelViews: non-identity physical mapping is wired through",
          "[pixel_views]") {
    PixelBufferPool pool;
    uint16_t buffer_sizes[] = {4};
    REQUIRE(pool.initialize(buffer_sizes, 1));

    const uint16_t physical[] = {7, 5, 3, 1};
    PixelViewSpec spec;
    spec.buffer_idx = 0;
    spec.size = 4;
    spec.storage_identity = true;
    spec.has_physical_mapping = true;
    spec.physical_identity = false;
    spec.physical_indices = physical;

    PixelViews views;
    REQUIRE(views.initialize(pool, &spec, 1));

    CHECK(views.at(0).has_physical_mapping());
    CHECK_FALSE(views.at(0).is_physical_identity());
    CHECK(views.at(0).physical_index(0) == 7);
    CHECK(views.at(0).physical_index(3) == 1);
}

// ---------------------------------------------------------------------------
// Validation failures
// ---------------------------------------------------------------------------

TEST_CASE("PixelViews: invalid buffer_idx fails", "[pixel_views]") {
    PixelBufferPool pool;
    uint16_t buffer_sizes[] = {8};
    REQUIRE(pool.initialize(buffer_sizes, 1));

    PixelViewSpec spec;
    spec.buffer_idx = PIXBUF_NONE;
    spec.size = 8;
    spec.storage_identity = true;

    PixelViews views;
    CHECK_FALSE(views.initialize(pool, &spec, 1));
    CHECK(views.count() == 0);
}

TEST_CASE("PixelViews: identity storage with size > buffer fails", "[pixel_views]") {
    PixelBufferPool pool;
    uint16_t buffer_sizes[] = {4};
    REQUIRE(pool.initialize(buffer_sizes, 1));

    PixelViewSpec spec;
    spec.buffer_idx = 0;
    spec.size = 8;  // > buffer_size(0) = 4
    spec.storage_identity = true;

    PixelViews views;
    CHECK_FALSE(views.initialize(pool, &spec, 1));
}

TEST_CASE("PixelViews: storage_identity with non-null storage_indices fails",
          "[pixel_views]") {
    PixelBufferPool pool;
    uint16_t buffer_sizes[] = {8};
    REQUIRE(pool.initialize(buffer_sizes, 1));

    const uint16_t storage[] = {0, 1, 2};
    PixelViewSpec spec;
    spec.buffer_idx = 0;
    spec.size = 3;
    spec.storage_identity = true;
    spec.storage_indices = storage;  // canonicality violation

    PixelViews views;
    CHECK_FALSE(views.initialize(pool, &spec, 1));
}

TEST_CASE("PixelViews: non-identity storage with null storage_indices fails",
          "[pixel_views]") {
    PixelBufferPool pool;
    uint16_t buffer_sizes[] = {8};
    REQUIRE(pool.initialize(buffer_sizes, 1));

    PixelViewSpec spec;
    spec.buffer_idx = 0;
    spec.size = 3;
    spec.storage_identity = false;
    spec.storage_indices = nullptr;

    PixelViews views;
    CHECK_FALSE(views.initialize(pool, &spec, 1));
}

TEST_CASE("PixelViews: physical_identity without has_physical_mapping fails",
          "[pixel_views]") {
    PixelBufferPool pool;
    uint16_t buffer_sizes[] = {8};
    REQUIRE(pool.initialize(buffer_sizes, 1));

    PixelViewSpec spec;
    spec.buffer_idx = 0;
    spec.size = 8;
    spec.storage_identity = true;
    spec.has_physical_mapping = false;
    spec.physical_identity = true;  // canonicality violation

    PixelViews views;
    CHECK_FALSE(views.initialize(pool, &spec, 1));
}

TEST_CASE("PixelViews: physical_identity with non-null physical_indices fails",
          "[pixel_views]") {
    PixelBufferPool pool;
    uint16_t buffer_sizes[] = {8};
    REQUIRE(pool.initialize(buffer_sizes, 1));

    const uint16_t physical[] = {0, 1, 2};
    PixelViewSpec spec;
    spec.buffer_idx = 0;
    spec.size = 3;
    spec.storage_identity = true;
    spec.has_physical_mapping = true;
    spec.physical_identity = true;
    spec.physical_indices = physical;  // canonicality violation

    PixelViews views;
    CHECK_FALSE(views.initialize(pool, &spec, 1));
}

TEST_CASE("PixelViews: non-identity physical with null physical_indices fails",
          "[pixel_views]") {
    PixelBufferPool pool;
    uint16_t buffer_sizes[] = {8};
    REQUIRE(pool.initialize(buffer_sizes, 1));

    PixelViewSpec spec;
    spec.buffer_idx = 0;
    spec.size = 3;
    spec.storage_identity = true;
    spec.has_physical_mapping = true;
    spec.physical_identity = false;
    spec.physical_indices = nullptr;

    PixelViews views;
    CHECK_FALSE(views.initialize(pool, &spec, 1));
}

TEST_CASE("PixelViews: failure mid-table cleans up earlier views",
          "[pixel_views]") {
    PixelBufferPool pool;
    uint16_t buffer_sizes[] = {8};
    REQUIRE(pool.initialize(buffer_sizes, 1));

    PixelViewSpec specs[2];
    specs[0].buffer_idx = 0;          // valid
    specs[0].size = 8;
    specs[0].storage_identity = true;
    specs[1].buffer_idx = PIXBUF_NONE;  // invalid: triggers reset
    specs[1].size = 8;
    specs[1].storage_identity = true;

    PixelViews views;
    CHECK_FALSE(views.initialize(pool, specs, 2));
    CHECK(views.count() == 0);
}

// ---------------------------------------------------------------------------
// reset / re-initialize
// ---------------------------------------------------------------------------

TEST_CASE("PixelViews: reset returns to empty", "[pixel_views]") {
    PixelBufferPool pool;
    uint16_t buffer_sizes[] = {8};
    REQUIRE(pool.initialize(buffer_sizes, 1));

    PixelViewSpec spec;
    spec.buffer_idx = 0;
    spec.size = 8;
    PixelViews views;
    REQUIRE(views.initialize(pool, &spec, 1));

    views.reset();

    CHECK(views.count() == 0);
}

TEST_CASE("PixelViews: re-initialize replaces previous table", "[pixel_views]") {
    PixelBufferPool pool;
    uint16_t buffer_sizes[] = {8, 16};
    REQUIRE(pool.initialize(buffer_sizes, 2));

    PixelViewSpec first[1];
    first[0].buffer_idx = 0;
    first[0].size = 8;
    PixelViews views;
    REQUIRE(views.initialize(pool, first, 1));

    PixelViewSpec second[2];
    second[0].buffer_idx = 0;
    second[0].size = 8;
    second[1].buffer_idx = 1;
    second[1].size = 16;
    REQUIRE(views.initialize(pool, second, 2));

    REQUIRE(views.count() == 2);
    CHECK(views.at(0).size() == 8);
    CHECK(views.at(1).size() == 16);
}
