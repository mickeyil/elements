#pragma once

#include <cstdint>


enum class DeviceMode : uint8_t {
    attached_controlled,
    detached_grace_hold,
    detached_blank,
    detached_background,
};
