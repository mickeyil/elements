#pragma once

#include <cstdint>

#include "handler_result.h"

class Playback;

namespace controller_link {

class WireReader;
class SessionHandler;
class BackgroundStore;

// Read-only handler for category 0x4_. QueryDeviceStatus assembles a fixed
// 14-byte payload from current Playback / BackgroundStore / device-mode
// state and returns it via ack_payload.

class StatusHandler {
public:
    StatusHandler(
        const Playback& playback,
        const BackgroundStore& store,
        const SessionHandler& session
    );

    HandlerResult handle(uint8_t opcode, WireReader& r);

    // TODO: device-mode tag. Today FirmwareApp owns DeviceMode and passes
    // a const pointer into ControllerConnection. Plumb the same way --
    // probably a getter passed as a function pointer or a small interface.
    // Sim mode tracking has to mirror the firmware tags so controller-side
    // status reads behave identically.

private:
    HandlerResult handle_query_device_status_(WireReader& r);

    const Playback&        _playback;
    const BackgroundStore& _store;
    const SessionHandler&  _session;

    // Scratch buffer for the ACK payload. Lives on the handler so the
    // pointer in HandlerResult::ack_payload stays valid until the parser
    // sends the frame.
    uint8_t _scratch[14] = {};
};

}  // namespace controller_link
