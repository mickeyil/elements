#pragma once

#include <WString.h>

#include <cstdint>

namespace firmware {

uint32_t make_boot_token();
String make_device_uid();

}  // namespace firmware
