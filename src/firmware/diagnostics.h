#pragma once

#include "runtime_state.h"

namespace firmware {

const char* yes_no(bool value);
void log_line(const char* fmt, ...);
void maybe_log_status(
    DiagnosticsState& diagnostics,
    const WifiState& wifi,
    const ControllerLinkState& link
);

}  // namespace firmware
