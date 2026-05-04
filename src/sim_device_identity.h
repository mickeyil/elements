#pragma once

#include "device_identity.h"

uint32_t make_sim_boot_token();
bool make_sim_device_identity(const char* uid, DeviceIdentity* out);
