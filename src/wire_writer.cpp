#include "wire_writer.h"

#include <cstring>

WireWriter::WireWriter(uint8_t* dst, size_t cap)
    : _dst(dst),
      _cap(dst == nullptr ? 0 : cap) {}

bool WireWriter::write_u8(uint8_t v)
{
    if (!_ok) return false;
    if (size_t(1) > _cap - _pos) { _ok = false; return false; }
    _dst[_pos++] = v;
    return true;
}

bool WireWriter::write_u16(uint16_t v)
{
    if (!_ok) return false;
    if (size_t(2) > _cap - _pos) { _ok = false; return false; }
    _dst[_pos]     = static_cast<uint8_t>(v);
    _dst[_pos + 1] = static_cast<uint8_t>(v >> 8);
    _pos += 2;
    return true;
}

bool WireWriter::write_u32(uint32_t v)
{
    if (!_ok) return false;
    if (size_t(4) > _cap - _pos) { _ok = false; return false; }
    _dst[_pos]     = static_cast<uint8_t>(v);
    _dst[_pos + 1] = static_cast<uint8_t>(v >> 8);
    _dst[_pos + 2] = static_cast<uint8_t>(v >> 16);
    _dst[_pos + 3] = static_cast<uint8_t>(v >> 24);
    _pos += 4;
    return true;
}

bool WireWriter::write_bytes(const uint8_t* src, size_t n)
{
    if (!_ok) return false;
    if (n > _cap - _pos) { _ok = false; return false; }
    if (n == 0) return true;
    if (src == nullptr) { _ok = false; return false; }
    std::memcpy(_dst + _pos, src, n);
    _pos += n;
    return true;
}
