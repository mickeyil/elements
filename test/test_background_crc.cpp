#include <cstdint>
#include <cstring>

#include <catch2/catch_test_macros.hpp>

#include "background_crc.h"

TEST_CASE("crc32 ieee matches standard check vector", "[background_crc]")
{
    static const char* kInput = "123456789";
    const uint32_t crc = elements::crc32_ieee(
        reinterpret_cast<const uint8_t*>(kInput),
        std::strlen(kInput)
    );
    REQUIRE(crc == 0xCBF43926u);
}

TEST_CASE("crc32 ieee incremental update matches one-shot", "[background_crc]")
{
    static const uint8_t kLeft[] = {'1', '2', '3', '4'};
    static const uint8_t kRight[] = {'5', '6', '7', '8', '9'};
    uint32_t crc = 0;
    crc = elements::crc32_ieee_continue(crc, kLeft, sizeof(kLeft));
    crc = elements::crc32_ieee_continue(crc, kRight, sizeof(kRight));
    REQUIRE(crc == 0xCBF43926u);
}
