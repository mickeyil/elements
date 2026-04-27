#pragma once

#include <cstdint>

#include "handler_result.h"
#include "tcp_transport.h"

class SyncedClock;

namespace controller_link {

class WireReader;

// Owns the attach state for the active controller connection and processes
// category 0x0_ commands:
//   0x00 SetProfile  -- apply hardware profile (allowed pre-attach)
//   0x01 Attach      -- claim the connection (id + frame port); records
//                       controller_ipv4 from TcpTransport::peer_address()
//                       so the outbound UDP frame loop has a destination.
//   0x02 SyncLease   -- u16 seq, u32 boot_token, i64 offset_us,
//                       u32 valid_for_ms; converts ms -> us and forwards
//                       to SyncedClock::apply_sync_offset.
//
// Other handlers read is_attached() to gate their own commands.
// firmware_app / sim main read controller_ipv4() and frame_port() for the
// outbound UDP path.

class SessionHandler {
public:
    SessionHandler(SyncedClock& clock, const TcpTransport& transport);

    // Dispatch entry for category 0x0_.
    HandlerResult handle(uint8_t opcode, WireReader& r);

    // True between successful Attach and disconnect / reset_session().
    bool is_attached() const { return _attached; }

    uint16_t device_id() const         { return _device_id; }
    uint16_t frame_port() const        { return _frame_port; }
    uint32_t controller_ipv4_be() const { return _controller_ipv4_be; }
    uint32_t boot_token() const        { return _boot_token; }

    // Wipe attach state and sync seq. Called on disconnect, on simulated
    // reboot, and on SetProfile-while-attached when the new profile
    // differs from the active one.
    void reset_session();

    // TODO: boot_token source. Today DeviceIdentity holds it on firmware.
    // SimSystemPlatform needs to write a fresh value here on simulated
    // reboot; pick: setter on this class, or pass DeviceIdentity& at
    // construction and re-read it.

private:
    HandlerResult handle_set_profile_(WireReader& r);
    HandlerResult handle_attach_(WireReader& r);
    HandlerResult handle_sync_lease_(WireReader& r);

    SyncedClock&        _clock;
    const TcpTransport& _transport;

    bool     _attached = false;
    uint16_t _device_id = 0;
    uint16_t _frame_port = 0;
    uint32_t _controller_ipv4_be = 0;
    uint16_t _last_sync_seq = 0;
    uint32_t _boot_token = 0;

    // TODO: SetProfile reaches into Playback (apply_hardware_profile). Two
    // options: hold a Playback& here, or extract a narrow ProfileSink
    // interface. The narrow interface costs a class and an extra include;
    // lean toward holding Playback& unless that creates a layer ick.
};

}  // namespace controller_link
