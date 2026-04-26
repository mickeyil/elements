#include <catch2/catch_test_macros.hpp>

#include <cstdint>

#include "../src/colors.h"
#include "../src/pixel_view.h"

namespace {
hsva_t with_h(float h) { return hsva_t(h, 0.5f, 0.5f, 1.0f); }
}  // namespace

// ---------------------------------------------------------------------------
// Empty / invalid input
// ---------------------------------------------------------------------------

TEST_CASE("PixelView: default-constructed is empty", "[pixel_view]") {
    PixelView v;
    CHECK(v.size() == 0);
    CHECK(v.empty());
    CHECK(v.is_storage_identity());
    CHECK_FALSE(v.has_physical_mapping());
    CHECK_FALSE(v.is_physical_identity());
}

TEST_CASE("PixelView: null backing with non-zero size fails", "[pixel_view]") {
    PixelView v;
    CHECK_FALSE(v.initialize(nullptr, 4));
    CHECK(v.size() == 0);
}

TEST_CASE("PixelView: non-identity physical mapping with null array fails",
          "[pixel_view]") {
    hsva_t backing[4];
    PixelView v;
    CHECK_FALSE(v.initialize(backing, 4,
                             /*storage_indices*/ nullptr,
                             /*has_physical_mapping*/ true,
                             /*physical_indices*/ nullptr,
                             /*physical_identity*/ false));
    CHECK(v.size() == 0);
}

// ---------------------------------------------------------------------------
// Storage mappings
// ---------------------------------------------------------------------------

TEST_CASE("PixelView: identity storage maps view[i] to backing[i]", "[pixel_view]") {
    hsva_t backing[8];
    PixelView v;
    REQUIRE(v.initialize(backing, 8));

    REQUIRE(v.size() == 8);
    CHECK(v.is_storage_identity());

    v[3] = with_h(120.0f);
    CHECK(backing[3].h == 120.0f);

    backing[5] = with_h(240.0f);
    CHECK(v[5].h == 240.0f);
}

TEST_CASE("PixelView: non-identity storage routes view[i] via the index array",
          "[pixel_view]") {
    hsva_t backing[6];
    const uint16_t storage[] = {5, 0, 3, 1};
    PixelView v;
    REQUIRE(v.initialize(backing, 4, storage));

    REQUIRE(v.size() == 4);
    CHECK_FALSE(v.is_storage_identity());

    v[0] = with_h(100.0f);  // -> backing[5]
    v[3] = with_h(200.0f);  // -> backing[1]
    CHECK(backing[5].h == 100.0f);
    CHECK(backing[1].h == 200.0f);
    CHECK(backing[0].h == 0.0f);  // not touched by the view
    CHECK(backing[2].h == 0.0f);
}

TEST_CASE("PixelView: storage_indices is copied (caller may free)", "[pixel_view]") {
    hsva_t backing[4];
    uint16_t* storage = new uint16_t[4]{0, 1, 2, 3};
    PixelView v;
    REQUIRE(v.initialize(backing, 4, storage));

    delete[] storage;  // view must not depend on this

    v[2] = with_h(60.0f);
    CHECK(backing[2].h == 60.0f);
}

// ---------------------------------------------------------------------------
// Physical mappings
// ---------------------------------------------------------------------------

TEST_CASE("PixelView: identity physical mapping returns i", "[pixel_view]") {
    hsva_t backing[4];
    PixelView v;
    REQUIRE(v.initialize(backing, 4,
                         /*storage_indices*/ nullptr,
                         /*has_physical_mapping*/ true,
                         /*physical_indices*/ nullptr,
                         /*physical_identity*/ true));

    CHECK(v.has_physical_mapping());
    CHECK(v.is_physical_identity());
    for (uint16_t i = 0; i < 4; i++) {
        CHECK(v.physical_index(i) == i);
    }
}

TEST_CASE("PixelView: non-identity physical mapping reads from the array",
          "[pixel_view]") {
    hsva_t backing[4];
    const uint16_t physical[] = {7, 3, 11, 1};
    PixelView v;
    REQUIRE(v.initialize(backing, 4,
                         /*storage_indices*/ nullptr,
                         /*has_physical_mapping*/ true,
                         /*physical_indices*/ physical,
                         /*physical_identity*/ false));

    CHECK(v.has_physical_mapping());
    CHECK_FALSE(v.is_physical_identity());
    CHECK(v.physical_index(0) == 7);
    CHECK(v.physical_index(1) == 3);
    CHECK(v.physical_index(2) == 11);
    CHECK(v.physical_index(3) == 1);
}

TEST_CASE("PixelView: physical_indices is copied (caller may free)", "[pixel_view]") {
    hsva_t backing[3];
    uint16_t* physical = new uint16_t[3]{2, 5, 9};
    PixelView v;
    REQUIRE(v.initialize(backing, 3,
                         /*storage*/ nullptr,
                         /*has_physical_mapping*/ true,
                         /*physical_indices*/ physical,
                         /*physical_identity*/ false));

    delete[] physical;
    CHECK(v.physical_index(0) == 2);
    CHECK(v.physical_index(1) == 5);
    CHECK(v.physical_index(2) == 9);
}

// ---------------------------------------------------------------------------
// Storage and physical mappings are independent
// ---------------------------------------------------------------------------

TEST_CASE("PixelView: identity storage + identity physical", "[pixel_view]") {
    hsva_t backing[4];
    PixelView v;
    REQUIRE(v.initialize(backing, 4, nullptr, true, nullptr, true));

    v[1] = with_h(40.0f);
    CHECK(backing[1].h == 40.0f);
    CHECK(v.physical_index(1) == 1);
}

TEST_CASE("PixelView: non-identity storage + identity physical are independent",
          "[pixel_view]") {
    hsva_t backing[6];
    const uint16_t storage[] = {5, 4, 3};
    PixelView v;
    REQUIRE(v.initialize(backing, 3, storage,
                         /*has_physical_mapping*/ true,
                         /*physical_indices*/ nullptr,
                         /*physical_identity*/ true));

    v[1] = with_h(60.0f);
    CHECK(backing[4].h == 60.0f);     // storage routed through indices
    CHECK(v.physical_index(1) == 1);  // physical is identity, unaffected
}

TEST_CASE("PixelView: identity storage + non-identity physical are independent",
          "[pixel_view]") {
    hsva_t backing[4];
    const uint16_t physical[] = {10, 11, 12, 13};
    PixelView v;
    REQUIRE(v.initialize(backing, 4,
                         /*storage*/ nullptr,
                         /*has_physical_mapping*/ true,
                         /*physical_indices*/ physical,
                         /*physical_identity*/ false));

    v[2] = with_h(30.0f);
    CHECK(backing[2].h == 30.0f);      // storage is identity
    CHECK(v.physical_index(2) == 12);  // physical via indices
}

TEST_CASE("PixelView: non-identity storage + non-identity physical are independent",
          "[pixel_view]") {
    hsva_t backing[6];
    const uint16_t storage[]  = {5, 4, 3};
    const uint16_t physical[] = {20, 21, 22};
    PixelView v;
    REQUIRE(v.initialize(backing, 3, storage,
                         /*has_physical_mapping*/ true,
                         /*physical_indices*/ physical,
                         /*physical_identity*/ false));

    v[0] = with_h(10.0f);
    CHECK(backing[5].h == 10.0f);
    CHECK(v.physical_index(0) == 20);
    CHECK(v.physical_index(1) == 21);
    CHECK(v.physical_index(2) == 22);
}

// ---------------------------------------------------------------------------
// clear / reset / re-initialize
// ---------------------------------------------------------------------------

TEST_CASE("PixelView: clear zeroes only pixels reachable through the view",
          "[pixel_view]") {
    hsva_t backing[6];
    for (uint16_t i = 0; i < 6; i++) backing[i] = with_h(50.0f * i);

    const uint16_t storage[] = {1, 3};
    PixelView v;
    REQUIRE(v.initialize(backing, 2, storage));

    v.clear();

    CHECK(backing[1].h == 0.0f);   // cleared
    CHECK(backing[3].h == 0.0f);   // cleared
    CHECK(backing[0].h == 0.0f);   // pre-existing 0
    CHECK(backing[2].h == 100.0f); // untouched
    CHECK(backing[4].h == 200.0f); // untouched
    CHECK(backing[5].h == 250.0f); // untouched
}

TEST_CASE("PixelView: reset returns to empty state", "[pixel_view]") {
    hsva_t backing[4];
    const uint16_t storage[] = {0, 1, 2, 3};
    PixelView v;
    REQUIRE(v.initialize(backing, 4, storage,
                         /*has_physical_mapping*/ true,
                         /*physical_indices*/ nullptr,
                         /*physical_identity*/ true));

    v.reset();

    CHECK(v.empty());
    CHECK(v.size() == 0);
    CHECK(v.is_storage_identity());        // post-reset: no storage indices
    CHECK_FALSE(v.has_physical_mapping());
    CHECK_FALSE(v.is_physical_identity());
}

TEST_CASE("PixelView: re-initialize replaces previous binding", "[pixel_view]") {
    hsva_t backing_a[4];
    hsva_t backing_b[8];

    PixelView v;
    REQUIRE(v.initialize(backing_a, 4));

    const uint16_t storage[] = {7, 6, 5};
    REQUIRE(v.initialize(backing_b, 3, storage));

    REQUIRE(v.size() == 3);
    v[0] = with_h(40.0f);
    CHECK(backing_b[7].h == 40.0f);
    CHECK_FALSE(v.is_storage_identity());
}
