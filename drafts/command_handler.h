#pragma once

#include <cstdint>

#include "handler_result.h"

class Playback;
class BackgroundStore;
class WireReader;

// The single inbound-command handler. CommandParser frames a message
// off the TCP stream and hands the opcode + payload here; this class
// owns the full opcode switch and the protocol semantics behind each
// command. One handler for the whole command space: v3 has ~13
// opcodes, too few to be worth a per-category split.
//
// Each handle_* method is a thin translator: parse the payload with
// WireReader, call the domain owner (Playback / BackgroundStore), map
// the result to an AckStatus. The real logic lives in the domain
// objects, not here. Holds no per-command state.
//
// Opcode map:
//   0x01 SetProfile        -> Playback hardware profile (u16 strip_length)
//   0x10 Load              -> Playback
//   0x11 Start             -> Playback
//   0x12 Jump              -> Playback
//   0x13 Pause             -> Playback
//   0x14 Resume            -> Playback
//   0x15 Stop              -> Playback
//   0x20 StoreBackground   -> BackgroundStore
//   0x21 ClearBackground   -> BackgroundStore
//   0x30 Reboot            -> HandlerResult::reboot(): ACK first, then
//                             the firmware loop / sim main fires the
//                             actual reboot after the ACK is flushed.
//                             No SystemPlatform reference here, so
//                             there's one owner of "when reboot fires."
//   0x40 QueryDeviceStatus -> 14-byte status snapshot in _status_scratch
//   0x41 Ping              -> ACK Ok; liveness evidence for ControllerLink
//
// REGISTER (0x00) is NOT handled here: it is a one-shot OUTBOUND
// message the link sends during connect. Inbound 0x00 -> UnknownCommand
// (controllers do not send REGISTER). 0x02 is reserved (old SyncLease,
// gone; sync moved to UDP).
//
// SetProfile may invalidate the loaded program if strip_length changes;
// Playback drops the program and the controller is expected to re-load.
//
// Const note: _playback and _store are held mutable because the
// playback/storage commands mutate them. QueryDeviceStatus only reads
// them; that read-only intent is convention here, not compiler-
// enforced. The per-category split that once made a const-only
// StatusHandler possible is gone.

class CommandHandler {
public:
    CommandHandler(Playback& playback, BackgroundStore& store);

    HandlerResult handle(uint8_t opcode, WireReader& r);

    // TODO: device-mode tag for QueryDeviceStatus. FirmwareApp owns
    // DeviceMode; plumb a const getter / tag callback here. Sim mode
    // tracking mirrors firmware tags.

    // TODO: Playback::handle_start / handle_resume / handle_jump are
    // void today (src/playback.cpp). v3 needs them to return status so
    // handle_start_ / _resume_ / _jump_ can ACK truthfully. Tracked in
    // drafts/TODO.md Playback.

    // TODO: Ping liveness-evidence signal path. handle_ping_ ACKs Ok,
    // but ControllerLink also needs to learn a Ping arrived so it can
    // reset _last_ping_us. Decide alongside the reboot signal path
    // (see drafts/controller_link.h poll()): either a new flag on
    // ControlSignal that CommandParser bubbles up, or a callback.

private:
    HandlerResult handle_set_profile_(WireReader& r);
    HandlerResult handle_load_(WireReader& r);
    HandlerResult handle_start_(WireReader& r);
    HandlerResult handle_jump_(WireReader& r);
    HandlerResult handle_pause_(WireReader& r);
    HandlerResult handle_resume_(WireReader& r);
    HandlerResult handle_stop_(WireReader& r);
    HandlerResult handle_store_background_(WireReader& r);
    HandlerResult handle_clear_background_(WireReader& r);
    HandlerResult handle_reboot_(WireReader& r);
    HandlerResult handle_query_device_status_(WireReader& r);
    HandlerResult handle_ping_(WireReader& r);

    Playback&        _playback;
    BackgroundStore& _store;

    // Scratch buffer for the QueryDeviceStatus ACK payload. Lives on
    // the handler so HandlerResult::ack_payload stays valid until the
    // parser sends the frame.
    uint8_t _status_scratch[14] = {};
};
