#pragma once

#include <cstddef>
#include <cstdint>

// IEEE CRC-32, the content fingerprint the controller and device must
// agree on (the controller computes it with Python's zlib.crc32).
// Implemented locally because shared code builds for both host and
// firmware, where no common framework implementation exists.

uint32_t crc32_ieee(const uint8_t* data, size_t len);

// Extend a prior crc32_ieee result with more data; feeding chunks in
// sequence equals one call over the concatenation. Start with 0.
uint32_t crc32_ieee_continue(uint32_t prior_crc, const uint8_t* data, size_t len);
