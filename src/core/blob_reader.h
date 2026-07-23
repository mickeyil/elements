#pragma once

#include <cstddef>
#include <cstdint>

// Bounded little-endian byte reader for parsing binary blobs, and the
// shared DecodeError category returned by parsing failures.
//
// Reads bytes from a caller-supplied buffer, tracking how far it has read.
// A read that would run past the end of the buffer fails and leaves the
// position alone. Multi-byte integers come out little-endian on any CPU.
// A null buffer behaves like an empty one: every read just fails.

// Reason a blob was rejected during decoding.
enum class DecodeError : uint8_t {
    Ok = 0,
    BadMagic,
    BadVersion,
    Truncated,
    TrailingBytes,
    InvalidField,
    OverCap,
    StripLengthMismatch,
    OutOfMemory,
};

// Printable name of the error, useful for logs. Never returns nullptr;
// unrecognized values map to "Unknown".
const char* decode_error_name(DecodeError err);

class BlobReader {
public:
    BlobReader(const uint8_t* data, size_t len);

    bool read_u8(uint8_t& out);
    bool read_u16_le(uint16_t& out);
    bool read_u32_le(uint32_t& out);
    bool read_f32_le(float& out);

    // Copy n bytes into dst. n == 0 is a successful no-op (dst may be null).
    // n > 0 with dst == nullptr is treated as a failed read.
    bool read_bytes(uint8_t* dst, size_t n);

    // Borrow n bytes from the buffer and advance past them. Returns nullptr
    // both for "out of bounds" and for "n == 0"; callers that need to
    // distinguish must check n > 0 before treating nullptr as a failure.
    const uint8_t* take(size_t n);

    bool done() const { return _pos == _len; }
    size_t remaining() const { return _len - _pos; }

private:
    const uint8_t* _data = nullptr;
    size_t _len = 0;
    size_t _pos = 0;
};
