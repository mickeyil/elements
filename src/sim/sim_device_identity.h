#pragma once

#include "device_identity.h"

// Caller must pass a non-null, non-empty UID of at most UID_SIZE
// bytes. Wire-shape and `sim-` prefix policy are enforced by the
// launcher; the device binary trusts its input.
DeviceIdentity make_sim_device_identity(const char* uid);
