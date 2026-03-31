#pragma once

#include <cstdint>

namespace firmware {

enum class DeviceMode : uint8_t {
    attached_controlled,
    detached_grace_hold,
    detached_blank,
    detached_background,
};

}  // namespace firmware
