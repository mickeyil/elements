#include "esp_device_identity.h"

#include <Esp.h>
#include <esp_system.h>

#include <cstdio>

namespace {

uint32_t make_boot_token_()
{
    uint32_t token = esp_random();
    if (token == 0) {
        token = 1;
    }
    return token;
}

void make_device_uid_(char* out, size_t out_size)
{
    const uint64_t chip_id = ESP.getEfuseMac();
    const uint8_t mac0 = static_cast<uint8_t>((chip_id >> 0) & 0xff);
    const uint8_t mac1 = static_cast<uint8_t>((chip_id >> 8) & 0xff);
    const uint8_t mac2 = static_cast<uint8_t>((chip_id >> 16) & 0xff);
    const uint8_t mac3 = static_cast<uint8_t>((chip_id >> 24) & 0xff);
    const uint8_t mac4 = static_cast<uint8_t>((chip_id >> 32) & 0xff);
    const uint8_t mac5 = static_cast<uint8_t>((chip_id >> 40) & 0xff);
    snprintf(
        out,
        out_size,
        "esp-%02x%02x%02x%02x%02x%02x",
        mac0,
        mac1,
        mac2,
        mac3,
        mac4,
        mac5
    );
}

}  // namespace

DeviceIdentity make_esp32_device_identity()
{
    DeviceIdentity identity;
    make_device_uid_(identity.uid, UID_CAPACITY);
    identity.boot_token = make_boot_token_();
    return identity;
}
