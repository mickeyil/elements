#pragma once

#include <cstddef>
#include <cstdint>

namespace firmware {

static constexpr size_t kDeviceUidCapacity = 24;

struct DeviceIdentity {
    char uid[kDeviceUidCapacity] = {};
    uint32_t boot_token = 0;
};

DeviceIdentity read_device_identity();

}  // namespace firmware
