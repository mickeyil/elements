#pragma once

#include <cstdint>

#include "handler_result.h"

class Playback;

class WireReader;
class BackgroundStore;

// Read-only handler for category 0x4_ (QueryDeviceStatus). Assembles a
// 14-byte payload from current Playback / BackgroundStore /
// device-mode state and returns it via HandlerResult::ack_payload.
// The controller already knows which device this is from the active
// TCP connection, so the payload doesn't include the UID.

class StatusHandler {
public:
    StatusHandler(const Playback& playback, const BackgroundStore& store);

    HandlerResult handle(uint8_t opcode, WireReader& r);

    // TODO: device-mode tag. Today FirmwareApp owns DeviceMode and
    // passes a const pointer. Plumb similarly here -- probably a small
    // function-pointer or a tag-getter callback. Sim mode tracking
    // mirrors firmware tags.

private:
    HandlerResult handle_query_device_status_(WireReader& r);

    const Playback&        _playback;
    const BackgroundStore& _store;

    // Scratch buffer for the ACK payload. Lives on the handler so the
    // pointer in HandlerResult::ack_payload stays valid until the
    // parser sends the frame.
    uint8_t _scratch[14] = {};
};
