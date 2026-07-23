#include "core/blob_reader.h"

#include <cstring>

BlobReader::BlobReader(const uint8_t* data, size_t len)
    : _data(data),
      _len(data == nullptr ? 0 : len),
      _pos(0) {}

bool BlobReader::read_u8(uint8_t& out)
{
    if (_pos >= _len) return false;
    out = _data[_pos++];
    return true;
}

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
    if (n == 0) return true;
    if (dst == nullptr) return false;
    std::memcpy(dst, _data + _pos, n);
    _pos += n;
    return true;
}

const uint8_t* BlobReader::take(size_t n)
{
    if (n > _len - _pos) return nullptr;
    if (n == 0) return nullptr;
    const uint8_t* p = _data + _pos;
    _pos += n;
    return p;
}

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
