#include <catch2/catch_test_macros.hpp>

#include <cstdint>

#include "app/device_status.h"
#include "app/link_protocol.h"

TEST_CASE("DeviceMode wire values are stable", "[device_status]") {
    CHECK(static_cast<uint8_t>(DeviceMode::AttachedControlled) == 0);
    CHECK(static_cast<uint8_t>(DeviceMode::DetachedGraceHold)  == 1);
    CHECK(static_cast<uint8_t>(DeviceMode::DetachedBlank)      == 2);
    CHECK(static_cast<uint8_t>(DeviceMode::DetachedBackground) == 3);
}

TEST_CASE("DeviceMode matches the link_protocol mirror", "[device_status]") {
    CHECK(static_cast<uint8_t>(DeviceMode::AttachedControlled) == MODE_ATTACHED_CONTROLLED);
    CHECK(static_cast<uint8_t>(DeviceMode::DetachedGraceHold)  == MODE_DETACHED_GRACE_HOLD);
    CHECK(static_cast<uint8_t>(DeviceMode::DetachedBlank)      == MODE_DETACHED_BLANK);
    CHECK(static_cast<uint8_t>(DeviceMode::DetachedBackground) == MODE_DETACHED_BACKGROUND);
}

TEST_CASE("DeviceStatus defaults to detached, no profile", "[device_status]") {
    DeviceStatus s;
    CHECK(s.mode == DeviceMode::DetachedBlank);
    CHECK(s.flags == 0);
}
