#include <catch2/catch_test_macros.hpp>

#include <cstdint>
#include <cstring>

#include "app/wire_writer.h"

TEST_CASE("WireWriter emits little-endian scalars", "[wire_writer]") {
    uint8_t buf[7] = {};
    WireWriter w(buf, sizeof(buf));

    REQUIRE(w.write_u8(0x12));
    REQUIRE(w.write_u16(0x5634));
    REQUIRE(w.write_u32(0xDEBC9A78u));
    CHECK(w.ok());
    CHECK(w.bytes_written() == 7);

    const uint8_t expected[] = {0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC, 0xDE};
    CHECK(std::memcmp(buf, expected, sizeof(expected)) == 0);
}

TEST_CASE("WireWriter emits f32 as little-endian IEEE bits", "[wire_writer]") {
    uint8_t buf[4] = {};
    WireWriter w(buf, sizeof(buf));

    REQUIRE(w.write_f32(1.0f));
    CHECK(w.ok());
    CHECK(w.bytes_written() == 4);

    const uint8_t expected[] = {0x00, 0x00, 0x80, 0x3F};  // 1.0f
    CHECK(std::memcmp(buf, expected, sizeof(expected)) == 0);
}

TEST_CASE("WireWriter copies byte slots", "[wire_writer]") {
    uint8_t buf[4] = {};
    WireWriter w(buf, sizeof(buf));

    const uint8_t name[] = {'a', 'b', 'c'};
    REQUIRE(w.write_bytes(name, sizeof(name)));
    REQUIRE(w.write_u8(0x00));
    CHECK(w.ok());
    CHECK(w.bytes_written() == 4);

    const uint8_t expected[] = {'a', 'b', 'c', 0x00};
    CHECK(std::memcmp(buf, expected, sizeof(expected)) == 0);
}

TEST_CASE("WireWriter write_bytes accepts zero-length", "[wire_writer]") {
    uint8_t buf[2] = {};
    WireWriter w(buf, sizeof(buf));

    CHECK(w.write_bytes(nullptr, 0));
    CHECK(w.ok());
    CHECK(w.bytes_written() == 0);

    CHECK_FALSE(w.write_bytes(nullptr, 1));
    CHECK_FALSE(w.ok());
}

TEST_CASE("WireWriter overflow is sticky and stops later writes", "[wire_writer]") {
    uint8_t buf[3] = {};
    WireWriter w(buf, sizeof(buf));

    REQUIRE(w.write_u8(0x11));
    CHECK(w.bytes_written() == 1);

    // 4 bytes into 2 remaining: overflow latches.
    CHECK_FALSE(w.write_u32(0x22334455u));
    CHECK_FALSE(w.ok());
    CHECK(w.bytes_written() == 1);

    // A u8 would fit in the remaining slot, but the latch makes it a no-op.
    CHECK_FALSE(w.write_u8(0x66));
    CHECK(w.bytes_written() == 1);
    CHECK(buf[1] == 0x00);
}

TEST_CASE("WireWriter null buffer rejects any write", "[wire_writer]") {
    WireWriter w(nullptr, 16);

    CHECK(w.ok());
    CHECK_FALSE(w.write_u8(0x01));
    CHECK_FALSE(w.ok());
    CHECK(w.bytes_written() == 0);
}
