#pragma once

#include <cstddef>
#include <cstdint>

#include "blob_limits.h"
#include "blob_reader.h"

struct Program;

static constexpr uint8_t BLOB_VERSION = 3;
static constexpr char    BLOB_MAGIC[4] = { 'E', 'L', 'E', 'M' };

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
