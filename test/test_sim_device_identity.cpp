#include "../src/sim_device_identity.h"

#include <catch2/catch_test_macros.hpp>

#include <cstring>

TEST_CASE("sim device identity copies uid and creates boot token", "[device_identity]")
{
    DeviceIdentity identity;
    REQUIRE(make_sim_device_identity("sim-test", &identity));
    CHECK(std::strcmp(identity.uid, "sim-test") == 0);
    CHECK(identity.boot_token != 0);
    CHECK(identity.protocol_version == PROTOCOL_VERSION);
}

TEST_CASE("sim device identity accepts a full wire-slot uid", "[device_identity]")
{
    DeviceIdentity identity;
    REQUIRE(make_sim_device_identity("1234567890123456", &identity));
    CHECK(std::strcmp(identity.uid, "1234567890123456") == 0);
}

TEST_CASE("sim device identity rejects invalid inputs", "[device_identity]")
{
    DeviceIdentity identity;
    CHECK_FALSE(make_sim_device_identity(nullptr, &identity));
    CHECK_FALSE(make_sim_device_identity("", &identity));
    CHECK_FALSE(make_sim_device_identity("12345678901234567", &identity));
    CHECK_FALSE(make_sim_device_identity("sim-test", nullptr));
}
