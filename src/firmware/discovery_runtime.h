#pragma once

#include <WiFiUdp.h>

#include "runtime_state.h"

namespace firmware {

void discovery_maybe_send_hello(
    WiFiUDP& discovery_udp,
    WifiState& wifi,
    DiagnosticsState& diag,
    RuntimeState& runtime
);

void discovery_poll_udp(
    WiFiUDP& discovery_udp,
    WifiState& wifi,
    DiagnosticsState& diag,
    RuntimeState& runtime
);

}  // namespace firmware
