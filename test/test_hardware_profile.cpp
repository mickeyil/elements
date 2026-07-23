#include <catch2/catch_test_macros.hpp>

#include "core/hardware_profile.h"

TEST_CASE("HardwareProfile: default constructed is invalid", "[hardware_profile]") {
    HardwareProfile profile;

    CHECK(profile.strip_length == 0);
    CHECK(profile.color_order == ColorOrder::RGB);
    CHECK(profile.gamma == IDENTITY_GAMMA);
    CHECK_FALSE(profile.is_valid());
}

TEST_CASE("HardwareProfile: valid strip length boundaries", "[hardware_profile]") {
    CHECK_FALSE(HardwareProfile(0).is_valid());
    CHECK(HardwareProfile(1).is_valid());
    CHECK(HardwareProfile(MAX_STRIP_PIXELS).is_valid());
}

TEST_CASE("HardwareProfile: rejects lengths above the cap", "[hardware_profile]") {
    const uint16_t too_long = static_cast<uint16_t>(MAX_STRIP_PIXELS + 1);

    CHECK_FALSE(HardwareProfile(too_long).is_valid());
}

TEST_CASE("HardwareProfile: constructor stores output fields", "[hardware_profile]") {
    HardwareProfile profile(42, ColorOrder::BGR, DEFAULT_GAMMA);

    CHECK(profile.strip_length == 42);
    CHECK(profile.color_order == ColorOrder::BGR);
    CHECK(profile.gamma == DEFAULT_GAMMA);
}

TEST_CASE("HardwareProfile: equality covers all fields", "[hardware_profile]") {
    const HardwareProfile base(42, ColorOrder::BGR, DEFAULT_GAMMA);

    CHECK(base == HardwareProfile(42, ColorOrder::BGR, DEFAULT_GAMMA));
    CHECK_FALSE(base == HardwareProfile(43, ColorOrder::BGR, DEFAULT_GAMMA));
    CHECK_FALSE(base == HardwareProfile(42, ColorOrder::RGB, DEFAULT_GAMMA));
    CHECK_FALSE(base == HardwareProfile(42, ColorOrder::BGR, IDENTITY_GAMMA));
}
