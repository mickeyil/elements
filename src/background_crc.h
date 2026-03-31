#pragma once

#include <cstddef>
#include <cstdint>

namespace elements {

uint32_t crc32_ieee_continue(uint32_t prior_crc, const uint8_t* data, size_t len);
uint32_t crc32_ieee(const uint8_t* data, size_t len);

}  // namespace elements
