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

TEST_CASE("sim device identity defaults to an empty version", "[device_identity]")
{
    const DeviceIdentity identity = make_sim_device_identity("sim-test");
    CHECK(identity.version[0] == '\0');
}

TEST_CASE("sim device identity copies the version", "[device_identity]")
{
    const DeviceIdentity identity = make_sim_device_identity("sim-test", "e029971+d");
    CHECK(std::strcmp(identity.version, "e029971+d") == 0);
}

TEST_CASE("sim device identity truncates an oversized version", "[device_identity]")
{
    // 30 chars; the buffer keeps VERSION_BUF_SIZE - 1 and stays terminated.
    const DeviceIdentity identity =
        make_sim_device_identity("sim-test", "0123456789abcdefghijklmnopqrst");
    CHECK(std::strlen(identity.version) == VERSION_BUF_SIZE - 1);
    CHECK(std::strncmp(identity.version, "0123456789abcdefghijklmnopqrst",
                       VERSION_BUF_SIZE - 1) == 0);
}
