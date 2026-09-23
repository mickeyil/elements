#pragma once

#include "platform/device_identity.h"

// version is the sim build's version (the generated elements_version.h
// in the sim binary); longer than VERSION_BUF_SIZE - 1 is truncated.
DeviceIdentity make_sim_device_identity(const char* uid, const char* version = "");
