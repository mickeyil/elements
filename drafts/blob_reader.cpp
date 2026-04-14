#include "blob_reader.h"

#include <cstring>

// ---------------------------------------------------------------------------
// BlobReader
// ---------------------------------------------------------------------------

// Constructor coerces a null buffer to zero length so every read fails
// cleanly via the bounds checks instead of dereferencing _data.
BlobReader::BlobReader(const uint8_t* data, size_t len)
    : _data(data),
      _len(data == nullptr ? 0 : len),
      _pos(0) {}

// Bounds checks are written as `n > _len - _pos` (not `_pos + n > _len`) so
// large `n` cannot overflow the addition. `_pos <= _len` is an invariant
// preserved by every read.

bool BlobReader::read_u8(uint8_t& out)
{
    if (_pos >= _len) return false;
    out = _data[_pos++];
    return true;
}

// Integer reads assemble the value byte-by-byte so the result is little-
// endian regardless of host byte order. Modern compilers optimize this to
// a single load on little-endian targets.

bool BlobReader::read_u16_le(uint16_t& out)
{
    if (size_t(2) > _len - _pos) return false;
    out = static_cast<uint16_t>(_data[_pos]) |
          static_cast<uint16_t>(static_cast<uint16_t>(_data[_pos + 1]) << 8);
    _pos += 2;
    return true;
}

bool BlobReader::read_u32_le(uint32_t& out)
{
    if (size_t(4) > _len - _pos) return false;
    out = static_cast<uint32_t>(_data[_pos]) |
          (static_cast<uint32_t>(_data[_pos + 1]) << 8) |
          (static_cast<uint32_t>(_data[_pos + 2]) << 16) |
          (static_cast<uint32_t>(_data[_pos + 3]) << 24);
    _pos += 4;
    return true;
}

// Float read: pull a little-endian u32 then bit-cast into float. memcpy is
// the standards-correct way to reinterpret the bit pattern.
bool BlobReader::read_f32_le(float& out)
{
    uint32_t bits = 0;
    if (!read_u32_le(bits)) return false;
    std::memcpy(&out, &bits, sizeof(out));
    return true;
}

bool BlobReader::read_bytes(uint8_t* dst, size_t n)
{
    if (n > _len - _pos) return false;
    if (n == 0) return true;            // no-op; dst may be nullptr
    if (dst == nullptr) return false;   // n > 0 with null dst is a caller error
    std::memcpy(dst, _data + _pos, n);
    _pos += n;
    return true;
}

const uint8_t* BlobReader::take(size_t n)
{
    if (n > _len - _pos) return nullptr;
    // Zero-length take: no meaningful pointer. Returning nullptr here also
    // avoids pointer arithmetic on a possibly-null _data. Callers that need
    // to distinguish "0 bytes requested" from "out of bounds" must check
    // their own n > 0 before treating nullptr as a failure.
    if (n == 0) return nullptr;
    const uint8_t* p = _data + _pos;
    _pos += n;
    return p;
}

bool   BlobReader::done() const      { return _pos == _len; }
size_t BlobReader::remaining() const { return _len - _pos; }

// ---------------------------------------------------------------------------
// decode_error_name
// ---------------------------------------------------------------------------

const char* decode_error_name(DecodeError err)
{
    switch (err) {
        case DecodeError::Ok:                  return "Ok";
        case DecodeError::BadMagic:            return "BadMagic";
        case DecodeError::BadVersion:          return "BadVersion";
        case DecodeError::Truncated:           return "Truncated";
        case DecodeError::TrailingBytes:       return "TrailingBytes";
        case DecodeError::InvalidField:        return "InvalidField";
        case DecodeError::OverCap:             return "OverCap";
        case DecodeError::StripLengthMismatch: return "StripLengthMismatch";
        case DecodeError::OutOfMemory:         return "OutOfMemory";
    }
    return "Unknown";
}
