#include <catch2/catch_test_macros.hpp>
#include <catch2/catch_approx.hpp>

#include <cstdint>
#include <cstring>

#include "core/blob_limits.h"
#include "core/blob_reader.h"

TEST_CASE("Blob limits expose v3 caps", "[blob_reader][blob_limits]") {
    CHECK(MAX_LAYER_COUNT == 32);
    CHECK(MAX_STRIP_PIXELS == 300);
    CHECK(MAX_BUFFER_COUNT == 256);
    CHECK(MAX_PIXEL_VIEW_COUNT == 512);
    CHECK(MAX_COPY_OP_COUNT == 512);
    CHECK(MAX_EVENTS_PER_LAYER == 1024);
    CHECK(MAX_EVENT_PARAMS_BYTES == 8192);
    CHECK(MAX_POOL_BYTES == 100 * 1024);
}

TEST_CASE("decode_error_name returns stable names", "[blob_reader]") {
    CHECK(std::strcmp(decode_error_name(DecodeError::Ok), "Ok") == 0);
    CHECK(std::strcmp(decode_error_name(DecodeError::BadMagic), "BadMagic") == 0);
    CHECK(std::strcmp(decode_error_name(DecodeError::BadVersion), "BadVersion") == 0);
    CHECK(std::strcmp(decode_error_name(DecodeError::Truncated), "Truncated") == 0);
    CHECK(std::strcmp(decode_error_name(DecodeError::TrailingBytes), "TrailingBytes") == 0);
    CHECK(std::strcmp(decode_error_name(DecodeError::InvalidField), "InvalidField") == 0);
    CHECK(std::strcmp(decode_error_name(DecodeError::OverCap), "OverCap") == 0);
    CHECK(std::strcmp(decode_error_name(DecodeError::StripLengthMismatch), "StripLengthMismatch") == 0);
    CHECK(std::strcmp(decode_error_name(DecodeError::OutOfMemory), "OutOfMemory") == 0);
    CHECK(std::strcmp(decode_error_name(static_cast<DecodeError>(255)), "Unknown") == 0);
}

TEST_CASE("BlobReader reads little-endian scalars", "[blob_reader]") {
    const uint8_t data[] = {
        0x12,
        0x34, 0x56,
        0x78, 0x9A, 0xBC, 0xDE,
        0x00, 0x00, 0x80, 0x3F,
    };
    BlobReader r(data, sizeof(data));

    uint8_t u8 = 0;
    uint16_t u16 = 0;
    uint32_t u32 = 0;
    float f32 = 0.0f;

    REQUIRE(r.read_u8(u8));
    CHECK(u8 == 0x12);
    REQUIRE(r.read_u16_le(u16));
    CHECK(u16 == 0x5634);
    REQUIRE(r.read_u32_le(u32));
    CHECK(u32 == 0xDEBC9A78u);
    REQUIRE(r.read_f32_le(f32));
    CHECK(f32 == Catch::Approx(1.0f));
    CHECK(r.done());
    CHECK(r.remaining() == 0);
}

TEST_CASE("BlobReader fails one byte past the end", "[blob_reader]") {
    const uint8_t data[] = {0xAA};
    BlobReader r(data, sizeof(data));

    uint8_t value = 0;
    REQUIRE(r.read_u8(value));
    CHECK_FALSE(r.read_u8(value));
    CHECK(r.done());
}

TEST_CASE("BlobReader rejects truncated multi-byte reads", "[blob_reader]") {
    const uint8_t data[] = {0xAA, 0xBB, 0xCC};

    uint16_t u16 = 0;
    BlobReader r16(data, 1);
    CHECK_FALSE(r16.read_u16_le(u16));
    CHECK(r16.remaining() == 1);

    uint32_t u32 = 0;
    BlobReader r32(data, 3);
    CHECK_FALSE(r32.read_u32_le(u32));
    CHECK(r32.remaining() == 3);

    float f32 = 0.0f;
    BlobReader rf32(data, 3);
    CHECK_FALSE(rf32.read_f32_le(f32));
    CHECK(rf32.remaining() == 3);
}

TEST_CASE("BlobReader leaves position unchanged on failed scalar reads", "[blob_reader]") {
    const uint8_t data[] = {0xAA, 0xBB, 0xCC};
    BlobReader r(data, sizeof(data));

    uint32_t value = 0;
    CHECK_FALSE(r.read_u32_le(value));
    CHECK(r.remaining() == 3);

    uint8_t first = 0;
    REQUIRE(r.read_u8(first));
    CHECK(first == 0xAA);
}

TEST_CASE("BlobReader read_bytes copies and bounds-checks", "[blob_reader]") {
    const uint8_t data[] = {1, 2, 3, 4};
    uint8_t dst[] = {0, 0, 0};
    BlobReader r(data, sizeof(data));

    REQUIRE(r.read_bytes(dst, 3));
    CHECK(dst[0] == 1);
    CHECK(dst[1] == 2);
    CHECK(dst[2] == 3);
    CHECK(r.remaining() == 1);

    CHECK_FALSE(r.read_bytes(dst, 2));
    CHECK(r.remaining() == 1);
}

TEST_CASE("BlobReader read_bytes handles zero and null destinations", "[blob_reader]") {
    const uint8_t data[] = {1, 2};
    BlobReader r(data, sizeof(data));

    CHECK(r.read_bytes(nullptr, 0));
    CHECK(r.remaining() == 2);
    CHECK_FALSE(r.read_bytes(nullptr, 1));
    CHECK(r.remaining() == 2);
}

TEST_CASE("BlobReader take borrows bytes and advances", "[blob_reader]") {
    const uint8_t data[] = {5, 6, 7};
    BlobReader r(data, sizeof(data));

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

TEST_CASE("BlobReader null buffer is empty", "[blob_reader]") {
    BlobReader r(nullptr, 12);

    uint8_t value = 0;
    CHECK_FALSE(r.read_u8(value));
    CHECK_FALSE(r.read_bytes(&value, 1));
    CHECK(r.take(1) == nullptr);
    CHECK(r.done());
    CHECK(r.remaining() == 0);
}
