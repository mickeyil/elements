#pragma once

#include <cstddef>
#include <cstdint>

// Bounded little-endian payload reader. Mechanics only -- typed reads, a
// remaining-byte query, and a require_empty() check. The "extra trailing
// bytes are an error" policy lives in handlers, not here.
//
// Every read returns false on under-run and leaves the cursor untouched.

namespace controller_link {

class WireReader {
public:
    WireReader(const uint8_t* data, size_t len);

    bool read_u8(uint8_t& out);
    bool read_u16(uint16_t& out);
    bool read_u32(uint32_t& out);
    bool read_i64(int64_t& out);
    bool read_f32(float& out);

    // Borrow n bytes and advance the cursor. Returns nullptr on under-run
    // and on n == 0.
    const uint8_t* take(size_t n);

    size_t remaining() const { return _len - _pos; }
    bool   done() const      { return _pos == _len; }

    // Handler-level convenience: assert no trailing bytes. v3 handlers call
    // this at the end of every command parse. Returns true if the payload
    // was fully consumed.
    bool require_empty() const { return done(); }

private:
    const uint8_t* _data;
    size_t _len;
    size_t _pos;
};

}  // namespace controller_link
