#pragma once

#include <cstdint>

#include "controller_connection.h"
#include "discovery_service.h"
#include "wifi_manager.h"

namespace firmware {

const char* yes_no(bool value);
void log_line(const char* fmt, ...);
void maybe_log_status(
    uint32_t& last_status_ms,
    const char* mode,
    const WifiSnapshot& wifi,
    const DiscoverySnapshot& discovery,
    const ConnectionSnapshot& connection
);

}  // namespace firmware
