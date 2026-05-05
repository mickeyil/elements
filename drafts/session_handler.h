#pragma once

#include <cstdint>

#include "handler_result.h"

class Playback;

class WireReader;

// Handles category 0x0_ inbound commands FROM the controller after
// the REGISTER handshake has completed. After sync moved off TCP onto
// its own UDP flow, this category is just one opcode:
//   0x01 SetProfile: u16 strip_length; applies to Playback's hardware
//                    profile.
//
// REGISTER (0x00) is NOT handled here; it's a one-shot outbound
// message sent by send_register() during connect, before
// CommandParser starts. The dispatch case for inbound 0x00 is
// AckStatus::UnknownCommand (controllers don't send REGISTER).
//
// 0x02 is reserved -- the old SyncLease lived there briefly during the
// v3 draft and is now gone.
//
// SetProfile may invalidate the currently loaded program if the strip
// length changes. Playback drops the program in that case; the
// controller is expected to re-load and re-start.
//
// TODO: SessionHandler now owns one opcode. It may fold into another
// handler (e.g., a renamed "ProfileHandler", or merged into Status as
// "device configuration"). Cosmetic; defer until the rest of v3 lands.

class SessionHandler {
public:
    explicit SessionHandler(Playback& playback);

    HandlerResult handle(uint8_t opcode, WireReader& r);

private:
    HandlerResult handle_set_profile_(WireReader& r);

    Playback& _playback;
};
