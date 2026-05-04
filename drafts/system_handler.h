#pragma once

#include <cstdint>

#include "handler_result.h"

class WireReader;

// Handler for category 0x3_. Today this is just Reboot, which returns
// HandlerResult::reboot() -- ACK first, signal via
// PollResult::reboot_requested. SystemHandler does NOT hold a
// SystemPlatform reference; the actual reboot is fired by the firmware
// loop / sim main after the ACK has been flushed. Keeping the platform
// out of the handler avoids two owners of "when does the reboot fire."

class SystemHandler {
public:
    SystemHandler() = default;

    HandlerResult handle(uint8_t opcode, WireReader& r);

private:
    HandlerResult handle_reboot_(WireReader& r);
};
