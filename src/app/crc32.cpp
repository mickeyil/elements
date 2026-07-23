#include "app/crc32.h"

namespace {

uint32_t update_byte_(uint32_t crc, uint8_t byte)
{
    crc ^= byte;
    for (int bit = 0; bit < 8; ++bit) {
        const uint32_t mask = -(crc & 1u);
        crc = (crc >> 1) ^ (0xEDB88320u & mask);
    }
    return crc;
}

}  // namespace

uint32_t crc32_ieee_continue(uint32_t prior_crc, const uint8_t* data, size_t len)
{
    uint32_t crc = prior_crc ^ 0xFFFFFFFFu;
    for (size_t i = 0; i < len; ++i) {
        crc = update_byte_(crc, data[i]);
    }
    return crc ^ 0xFFFFFFFFu;
}

uint32_t crc32_ieee(const uint8_t* data, size_t len)
{
    return crc32_ieee_continue(0, data, len);
}
