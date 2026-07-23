#pragma once

#include <cstddef>
#include <cstdint>

#include "core/blob_limits.h"
#include "core/blob_reader.h"

struct Program;

static constexpr uint8_t BLOB_VERSION = 3;
static constexpr char    BLOB_MAGIC[4] = { 'E', 'L', 'E', 'M' };

// Fixed blob prefix + header, the part peek_blob_header reads:
//   magic=4B | version=1B | flags=1B | fps=1B | layers=1B |
//   strip_length=2B | buffers=2B | views=2B | copy_ops=2B | duration=4B
static constexpr size_t BLOB_HEADER_BYTES = 20;

// Cheap admission check without a full decode: is this a
// current-version blob, and does it require a synced clock? False on
// a truncated header, foreign magic/version, or reserved flag bits.
bool peek_blob_header(const uint8_t* blob, size_t len,
                      uint16_t& strip_length_out, bool& requires_sync_out);

// Parse a blob into a Program. Returns nullptr on failure and writes the
// reason to `*err_out` (if non-null). The blob's strip length must match
// `profile_strip_length` exactly.
//
// The returned Program owns every nested allocation. Free with
// `free_program()`.
Program* decode_program(
    const uint8_t* blob,
    size_t blob_len,
    uint16_t profile_strip_length,
    DecodeError* err_out = nullptr
);
