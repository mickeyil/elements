#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

#include "animation_store.h"
#include "handler_result.h"

#include "../src/playback.h"

class WireReader;

// The single inbound-command handler. CommandParser frames a message
// off the TCP stream and hands the opcode + payload here; this class
// owns the full opcode switch and the protocol semantics behind each
// command. One handler for the whole command space: v3 has ~15
// opcodes, too few to be worth a per-category split.
//
// Each handle_* method is a thin translator: parse the payload with
// WireReader, call the domain owner (Playback / AnimationStore), map
// the result to an AckStatus. The real logic lives in the domain
// objects, not here.
//
// Opcode map:
//   0x01 SetProfile           Persist the strip length to flash. If it
//                             equals the current profile, no-op. If it
//                             differs, persist it and return
//                             HandlerResult::reboot() so the new
//                             geometry takes effect from a clean boot.
//   0x10 Load                 Decode a live program blob into Playback.
//   0x11 Start                Start a loaded program (synced anchor or
//                             unsynced).
//   0x12 Jump                 Seek the live program.
//   0x13 Pause                Pause the live program.
//   0x14 Resume               Resume the live program.
//   0x15 Stop                 Stop the live program.
//   0x16 PlayLocalAnimation   Play the stored animation at a given
//                             play-order position, looping.
//   0x20 StoreAnimation       Add a blob to AnimationStore.
//   0x21 EraseAnimation       Remove a stored animation by id.
//   0x22 SetAnimationOrder    Replace the play order (a permutation of
//                             the stored ids).
//   0x30 Reboot               ACK, then the firmware loop / sim main
//                             fires the actual reboot after the ACK is
//                             flushed. No SystemPlatform reference
//                             here, so there is one owner of "when
//                             reboot fires."
//   0x40 QueryDeviceStatus    Snapshot: device mode, flags, and the
//                             count of stored animations.
//   0x41 Ping                 ACK Ok; liveness evidence for
//                             ControllerLink.
//   0x42 QueryLocalAnimations The stored animations in play order, one
//                             {crc32 id, strip_length} pair each.
//
// REGISTER (0x00) is NOT handled here: it is a one-shot OUTBOUND
// message the link sends during connect. Inbound 0x00 -> UnknownCommand
// (controllers do not send REGISTER). 0x02 is reserved (old SyncLease,
// gone; sync moved to UDP).
//
// Const note: _playback and _store are held mutable because most
// commands mutate them. The two query opcodes only read; that
// read-only intent is convention here, not compiler-enforced.

// QueryLocalAnimations wire entry: crc32 id (4) + strip_length (2).
constexpr size_t QUERY_ANIMATION_ENTRY_SIZE = 6;

// Scratch sized for the largest query reply, QueryLocalAnimations:
// a u16 count followed by one entry per stored animation.
constexpr size_t REPLY_SCRATCH_SIZE =
    2 + MAX_STORED_ANIMATIONS * QUERY_ANIMATION_ENTRY_SIZE;

class CommandHandler {
public:
    CommandHandler(Playback& playback, AnimationStore& store);

    HandlerResult handle(uint8_t opcode, WireReader& r);

    // TODO: profile persistence target. handle_set_profile_ must write
    // the strip length somewhere durable (NVS on ESP, a file on sim)
    // and the boot path must read it back before first render. That
    // interface is not yet drafted; SetProfile cannot be finished
    // without it.

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
    HandlerResult handle_play_local_animation_(WireReader& r);
    HandlerResult handle_store_animation_(WireReader& r);
    HandlerResult handle_erase_animation_(WireReader& r);
    HandlerResult handle_set_animation_order_(WireReader& r);
    HandlerResult handle_reboot_(WireReader& r);
    HandlerResult handle_query_device_status_(WireReader& r);
    HandlerResult handle_ping_(WireReader& r);
    HandlerResult handle_query_local_animations_(WireReader& r);

    Playback&       _playback;
    AnimationStore& _store;

    // Scratch for query ACK payloads (QueryDeviceStatus,
    // QueryLocalAnimations). Lives on the handler so the pointer in
    // HandlerResult::ack_payload stays valid until the parser sends
    // the frame.
    uint8_t _reply_scratch[REPLY_SCRATCH_SIZE] = {};
};

// Reference sketch for the storage load path, used by
// handle_play_local_animation_ and by boot selection. Storage loads
// are rare and the blob bytes are dead the moment Playback::handle_load
// has decoded them into a Program, so the blob is staged in a short-
// lived local vector: allocated for this one load, freed at scope
// exit. No resident staging buffer, no allocation churn. Starting and
// looping the loaded program is a separate playback-coordinator step.
inline bool load_stored_animation_(AnimationStore& store,
                                   Playback& playback,
                                   size_t index)
{
    const AnimationEntry e = store.entry(index);

    std::vector<uint8_t> blob(e.blob_len);
    if (!store.read_blob(e.id, blob.data(), blob.size())) {
        return false;
    }

    return playback.handle_load(blob.data(), blob.size());
    // blob frees here; the decoded Program now lives inside Playback.
}
