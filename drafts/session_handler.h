#pragma once

#include <cstdint>

#include "handler_result.h"

class SyncedClock;

namespace controller_link {

class WireReader;

// Handles category 0x0_ inbound commands FROM the controller after the
// DEVICE_HELLO handshake has completed:
//   0x01 SetProfile -- u16 strip_length; applies to Playback's hardware
//                      profile.
//   0x02 SyncLease  -- u16 seq, u32 boot_token, i64 offset_us,
//                      u32 valid_for_ms; converts ms -> us and forwards
//                      to SyncedClock::apply_sync_offset.
//
// DEVICE_HELLO (0x00) is NOT handled here -- it's a one-shot outbound
// message sent by send_device_hello() during connect, before
// CommandParser starts. The dispatch case for inbound 0x00 is
// AckStatus::UnknownCommand (controllers don't send DEVICE_HELLO).
//
// SetProfile may invalidate the currently loaded program if the strip
// length changes. Playback drops the program in that case; the
// controller is expected to re-load and re-start.

class SessionHandler {
public:
    explicit SessionHandler(SyncedClock& clock);

    HandlerResult handle(uint8_t opcode, WireReader& r);

    // Boot token, sent in DEVICE_HELLO and validated against
    // controller-supplied SyncLease packets to reject stale leases
    // from a previous boot.
    uint32_t boot_token() const { return _boot_token; }
    void set_boot_token(uint32_t t) { _boot_token = t; }

    // Reset session-scoped state (sync seq counter). Called on
    // disconnect and on simulated reboot.
    void reset_session();

    // TODO: who feeds boot_token? Today firmware::DeviceIdentity holds
    // it; SimSystemPlatform regenerates it on simulated reboot. Either
    // pass an identity reference at construction, or expose set_boot_token
    // and let the platform impl call it. Setter is simpler.

    // TODO: SetProfile applies the hardware profile to Playback. Decide
    // whether SessionHandler holds a Playback& or a narrower
    // ProfileSink interface. Playback& is simpler; the narrow
    // interface costs an extra class for one method.

private:
    HandlerResult handle_set_profile_(WireReader& r);
    HandlerResult handle_sync_lease_(WireReader& r);

    SyncedClock& _clock;
    uint32_t _boot_token = 0;
    uint16_t _last_sync_seq = 0;
};

}  // namespace controller_link
