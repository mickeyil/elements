#pragma once

#include <WString.h>

#include <cstdint>

namespace firmware {

struct DeviceIdentity {
    String uid;
    uint32_t boot_token = 0;
};

DeviceIdentity read_device_identity();

}  // namespace firmware
