#pragma once

#include <cstddef>
#include <cstdint>

// Bounded little-endian payload writer. Mirror of WireReader for the
// outbound side: handlers fill a caller-owned reply buffer through this,
// CommandProcessor reads bytes_written() and frames it.
//
// Sticky ok(): once a write would overflow, every subsequent write is a
// no-op and ok() stays false. Handlers fill the whole reply optimistically
// and check once at the end.

class WireWriter
{
public:
    WireWriter(uint8_t* dst, size_t cap);

    bool write_u8(uint8_t);
    bool write_u16(uint16_t);
    bool write_u32(uint32_t);

    // Copy n bytes verbatim. Used for fixed-size ASCII slots (name[32]).
    bool write_bytes(const uint8_t* src, size_t n);

    size_t bytes_written() const { return _pos; }
    bool   ok() const            { return _ok; }

private:
    uint8_t* _dst;
    size_t   _cap;
    size_t   _pos = 0;
    bool     _ok  = true;
};
