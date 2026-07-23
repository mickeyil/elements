#include "platform/sim/sim_device_identity.h"

#include <catch2/catch_test_macros.hpp>

#include <cstring>

TEST_CASE("sim device identity copies uid and creates boot token", "[device_identity]")
{
    const DeviceIdentity identity = make_sim_device_identity("sim-test");
    CHECK(std::strcmp(identity.uid, "sim-test") == 0);
    CHECK(identity.boot_token != 0);
}

TEST_CASE("sim device identity accepts a full wire-slot uid", "[device_identity]")
{
    const DeviceIdentity identity = make_sim_device_identity("1234567890123456");
    CHECK(std::strcmp(identity.uid, "1234567890123456") == 0);
}
