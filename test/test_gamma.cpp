#include <catch2/catch_test_macros.hpp>
#include <cmath>
#include "../src/gamma.h"

// ---------------------------------------------------------------------------
// Identity
// ---------------------------------------------------------------------------

TEST_CASE("GammaCorrection: default ctor is identity", "[gamma]") {
    GammaCorrection gc;
    CHECK(gc.gamma() == IDENTITY_GAMMA);
    for (int n = 0; n < 256; n++) {
        rgb_t c = gc.correct(rgb_t((uint8_t)n, (uint8_t)n, (uint8_t)n));
        CHECK(c.r == n);
        CHECK(c.g == n);
        CHECK(c.b == n);
    }
}

TEST_CASE("GammaCorrection: set_gamma(1.0) is identity", "[gamma]") {
    GammaCorrection gc;
    gc.set_gamma(IDENTITY_GAMMA);
    CHECK(gc.gamma() == IDENTITY_GAMMA);
    CHECK(gc.correct(rgb_t(0, 128, 255)).r == 0);
    CHECK(gc.correct(rgb_t(0, 128, 255)).g == 128);
    CHECK(gc.correct(rgb_t(0, 128, 255)).b == 255);
}

// ---------------------------------------------------------------------------
// Default curve (2.8)
// ---------------------------------------------------------------------------

TEST_CASE("GammaCorrection: 2.8 preserves 0 and 255", "[gamma]") {
    GammaCorrection gc;
    gc.set_gamma(DEFAULT_GAMMA);
    CHECK(gc.correct(rgb_t(0, 0, 0)).r == 0);
    CHECK(gc.correct(rgb_t(255, 255, 255)).r == 255);
}

TEST_CASE("GammaCorrection: 2.8 darkens midpoint", "[gamma]") {
    GammaCorrection gc;
    gc.set_gamma(DEFAULT_GAMMA);
    uint8_t mid = gc.correct(rgb_t(128, 0, 0)).r;
    CHECK(mid > 0);
    CHECK(mid < 128);
}

TEST_CASE("GammaCorrection: 2.8 matches golden value at index 128", "[gamma]") {
    // powf(128/255, 2.8) * 255 + 0.5 = 37.55... -> 37 after truncation.
    // Matches the pre-baked table that lived in src/colors.cpp.
    GammaCorrection gc;
    gc.set_gamma(DEFAULT_GAMMA);
    CHECK(gc.correct(rgb_t(128, 128, 128)).r == 37);
}

// ---------------------------------------------------------------------------
// Accepted boundary
// ---------------------------------------------------------------------------

TEST_CASE("GammaCorrection: MAX_SUPPORTED_GAMMA is accepted", "[gamma]") {
    GammaCorrection gc;
    gc.set_gamma(MAX_SUPPORTED_GAMMA);
    CHECK(gc.gamma() == MAX_SUPPORTED_GAMMA);
    CHECK(gc.correct(rgb_t(0, 0, 0)).r == 0);
    CHECK(gc.correct(rgb_t(255, 255, 255)).r == 255);
}

// ---------------------------------------------------------------------------
// Rejected values leave prior state intact
// ---------------------------------------------------------------------------

static void check_unchanged_after_reject(float bad) {
    GammaCorrection gc;
    gc.set_gamma(DEFAULT_GAMMA);
    const float prior_gamma = gc.gamma();
    const uint8_t prior_mid = gc.correct(rgb_t(128, 0, 0)).r;

    gc.set_gamma(bad);

    CHECK(gc.gamma() == prior_gamma);
    CHECK(gc.correct(rgb_t(128, 0, 0)).r == prior_mid);
}

TEST_CASE("GammaCorrection: zero is rejected", "[gamma]") {
    check_unchanged_after_reject(0.0f);
}

TEST_CASE("GammaCorrection: negative is rejected", "[gamma]") {
    check_unchanged_after_reject(-1.0f);
}

TEST_CASE("GammaCorrection: above MAX is rejected", "[gamma]") {
    check_unchanged_after_reject(MAX_SUPPORTED_GAMMA + 0.01f);
}

// ---------------------------------------------------------------------------
// Reset path
// ---------------------------------------------------------------------------

TEST_CASE("GammaCorrection: set_identity resets after set_gamma(2.8)", "[gamma]") {
    GammaCorrection gc;
    gc.set_gamma(DEFAULT_GAMMA);
    REQUIRE(gc.gamma() == DEFAULT_GAMMA);

    gc.set_identity();

    CHECK(gc.gamma() == IDENTITY_GAMMA);
    for (int n = 0; n < 256; n++) {
        CHECK(gc.correct(rgb_t((uint8_t)n, 0, 0)).r == n);
    }
}
