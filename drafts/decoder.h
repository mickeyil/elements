#pragma once

// Draft-only API.
//
// Decoder for blob format v3. The byte contract is in blob_format.md.
// Background and integration notes are in decoder.md. The bounded byte
// reader and DecodeError category live in blob_reader.h so per-animation
// from_blob() factories can depend on them without including this file.

#include <cstddef>
#include <cstdint>

#include "blob_limits.h"
#include "blob_reader.h"

struct Program;

static constexpr uint8_t kBlobVersion = 3;
static constexpr char    kBlobMagic[4] = { 'E', 'L', 'E', 'M' };

// Parse a v3 blob into a Program. Returns nullptr on any failure.
//
// On failure, *err_out (if non-null) is set to the failure category.
// On success, *err_out is set to DecodeError::Ok.
//
// `profile_strip_length` is the active HardwareProfile strip length. The
// blob's header strip_length must equal it exactly; otherwise the result
// is StripLengthMismatch. A profile_strip_length of 0 is rejected as
// InvalidField.
//
// The returned Program is heap-allocated and owns every nested allocation:
// pool, views, copy ops, layers, events, and per-event Animation instances.
// Free with free_program() declared in program_structs.h.
Program* decode_program(
    const uint8_t* blob,
    size_t blob_len,
    uint16_t profile_strip_length,
    DecodeError* err_out = nullptr
);
