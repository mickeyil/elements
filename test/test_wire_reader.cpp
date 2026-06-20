#include <catch2/catch_test_macros.hpp>
#include <catch2/catch_approx.hpp>

#include <cstdint>
#include <cstring>

#include "../src/wire_reader.h"

TEST_CASE("WireReader reads little-endian scalars", "[wire_reader]") {
    const uint8_t data[] = {
        0x12,
        0x34, 0x56,
        0x78, 0x9A, 0xBC, 0xDE,
        0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08,
        0x00, 0x00, 0x80, 0x3F,
    };
    WireReader r(data, sizeof(data));

    uint8_t u8 = 0;
    uint16_t u16 = 0;
    uint32_t u32 = 0;
    int64_t i64 = 0;
    float f32 = 0.0f;

    REQUIRE(r.read_u8(u8));
    CHECK(u8 == 0x12);
    REQUIRE(r.read_u16(u16));
    CHECK(u16 == 0x5634);
    REQUIRE(r.read_u32(u32));
    CHECK(u32 == 0xDEBC9A78u);
    REQUIRE(r.read_i64(i64));
    CHECK(i64 == 0x0807060504030201);
    REQUIRE(r.read_f32(f32));
    CHECK(f32 == Catch::Approx(1.0f));
    CHECK(r.done());
    CHECK(r.require_empty());
    CHECK(r.remaining() == 0);
}

TEST_CASE("WireReader read_i64 preserves sign", "[wire_reader]") {
    const uint8_t data[] = {0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF};
    WireReader r(data, sizeof(data));

    int64_t value = 0;
    REQUIRE(r.read_i64(value));
    CHECK(value == -1);
}

TEST_CASE("WireReader fails one byte past the end", "[wire_reader]") {
    const uint8_t data[] = {0xAA};
    WireReader r(data, sizeof(data));

    uint8_t value = 0;
    REQUIRE(r.read_u8(value));
    CHECK_FALSE(r.read_u8(value));
    CHECK(r.done());
}

TEST_CASE("WireReader rejects truncated multi-byte reads", "[wire_reader]") {
    const uint8_t data[] = {1, 2, 3, 4, 5, 6, 7};

    uint16_t u16 = 0;
    WireReader r16(data, 1);
    CHECK_FALSE(r16.read_u16(u16));
    CHECK(r16.remaining() == 1);

    uint32_t u32 = 0;
    WireReader r32(data, 3);
    CHECK_FALSE(r32.read_u32(u32));
    CHECK(r32.remaining() == 3);

    int64_t i64 = 0;
    WireReader r64(data, 7);
    CHECK_FALSE(r64.read_i64(i64));
    CHECK(r64.remaining() == 7);

    float f32 = 0.0f;
    WireReader rf32(data, 3);
    CHECK_FALSE(rf32.read_f32(f32));
    CHECK(rf32.remaining() == 3);
}

TEST_CASE("WireReader take borrows bytes and advances", "[wire_reader]") {
    const uint8_t data[] = {5, 6, 7};
    WireReader r(data, sizeof(data));

    const uint8_t* taken = r.take(2);
    REQUIRE(taken != nullptr);
    CHECK(taken[0] == 5);
    CHECK(taken[1] == 6);
    CHECK(r.remaining() == 1);

    CHECK(r.take(2) == nullptr);
    CHECK(r.remaining() == 1);
    CHECK(r.take(0) == nullptr);
    CHECK(r.remaining() == 1);
}

TEST_CASE("WireReader require_empty reports trailing bytes", "[wire_reader]") {
    const uint8_t data[] = {0xAA, 0xBB};
    WireReader r(data, sizeof(data));

    uint8_t value = 0;
    REQUIRE(r.read_u8(value));
    CHECK_FALSE(r.require_empty());
    REQUIRE(r.read_u8(value));
    CHECK(r.require_empty());
}

TEST_CASE("WireReader null buffer is empty", "[wire_reader]") {
    WireReader r(nullptr, 12);

    uint8_t value = 0;
    CHECK_FALSE(r.read_u8(value));
    CHECK(r.take(1) == nullptr);
    CHECK(r.done());
    CHECK(r.require_empty());
    CHECK(r.remaining() == 0);
}
