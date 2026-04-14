#pragma once

// Draft-only.
//
// Bounded little-endian byte reader and decode-error category shared by the
// firmware decoder and per-animation from_blob() factories. Lives in its own
// header so anim_*.h files can depend on the reader/error API without pulling
// in the full decoder entry point. Implementation is in blob_reader.cpp.

#include <cstddef>
#include <cstdint>

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

// Stable identifier for a DecodeError, suitable for serial logs and ACK
// diagnostics. Returns a static string; never nullptr.
const char* decode_error_name(DecodeError err);

// Bounded little-endian byte reader. Every read returns false if it would
// advance past the end of the buffer. All multi-byte reads assemble values
// byte-by-byte; the result is little-endian regardless of host byte order.
//
// Constructor coerces a null buffer to zero length so every read fails
// cleanly via the bounds check instead of dereferencing _data.
class BlobReader {
public:
    BlobReader(const uint8_t* data, size_t len);

    bool read_u8(uint8_t& out);
    bool read_u16_le(uint16_t& out);
    bool read_u32_le(uint32_t& out);
    bool read_f32_le(float& out);

    // Copy `n` bytes into `dst`. Bounds-checked. Special cases:
    //   - n == 0 is a successful no-op; dst may be nullptr
    //   - n > 0 with dst == nullptr returns false (caller error)
    bool read_bytes(uint8_t* dst, size_t n);

    // Borrow `n` bytes from the buffer and advance past them. Returns
    // nullptr both for "out of bounds" and for "n == 0" — the latter to
    // avoid pointer arithmetic on a possibly-null buffer. Callers that need
    // to distinguish the two cases must check `n > 0` themselves before
    // treating nullptr as a failure:
    //
    //     const uint8_t* p = r.take(params_size);
    //     if (params_size > 0 && p == nullptr) return Truncated;
    //
    // The returned pointer is valid for the lifetime of the underlying
    // buffer the reader was constructed over.
    const uint8_t* take(size_t n);

    bool   done() const;
    size_t remaining() const;

private:
    const uint8_t* _data;
    size_t         _len;
    size_t         _pos;
};
