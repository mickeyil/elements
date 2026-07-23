#include "app/wire_reader.h"

#include <cstring>

WireReader::WireReader(const uint8_t* data, size_t len)
    : _data(data),
      _len(data == nullptr ? 0 : len),
      _pos(0) {}

bool WireReader::read_u8(uint8_t& out)
{
    if (_pos >= _len) return false;
    out = _data[_pos++];
    return true;
}

bool WireReader::read_u16(uint16_t& out)
{
    if (size_t(2) > _len - _pos) return false;
    out = static_cast<uint16_t>(_data[_pos]) |
          static_cast<uint16_t>(static_cast<uint16_t>(_data[_pos + 1]) << 8);
    _pos += 2;
    return true;
}

bool WireReader::read_u32(uint32_t& out)
{
    if (size_t(4) > _len - _pos) return false;
    out = static_cast<uint32_t>(_data[_pos]) |
          (static_cast<uint32_t>(_data[_pos + 1]) << 8) |
          (static_cast<uint32_t>(_data[_pos + 2]) << 16) |
          (static_cast<uint32_t>(_data[_pos + 3]) << 24);
    _pos += 4;
    return true;
}

bool WireReader::read_i64(int64_t& out)
{
    if (size_t(8) > _len - _pos) return false;
    uint64_t bits = 0;
    for (int i = 0; i < 8; ++i) {
        bits |= static_cast<uint64_t>(_data[_pos + i]) << (8 * i);
    }
    std::memcpy(&out, &bits, sizeof(out));
    _pos += 8;
    return true;
}

bool WireReader::read_f32(float& out)
{
    uint32_t bits = 0;
    if (!read_u32(bits)) return false;
    std::memcpy(&out, &bits, sizeof(out));
    return true;
}

const uint8_t* WireReader::take(size_t n)
{
    if (n > _len - _pos) return nullptr;
    if (n == 0) return nullptr;
    const uint8_t* p = _data + _pos;
    _pos += n;
    return p;
}
