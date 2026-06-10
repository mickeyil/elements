#pragma once

#include <cstddef>
#include <cstdint>

#include "../src/animation_store.h"
#include "app_context.h"

class WireReader;

// ACK status carried in the outbound 0x80 reply. Wire encoding is one
// byte; values are part of the controller-link protocol. The enum lives
// here because handlers produce it; the wire definition is in
// src/link_protocol.h via mirrored constants.
enum class AckStatus : uint8_t {
    Ok              = 0,
    Error           = 1,
    WrongState      = 2,
    ProfileMismatch = 3,
    BadPayload      = 4,
    UnknownCommand  = 5,
    Unsynced        = 6,
};

// What every handler returns to the processor. The reply payload is
// written into a caller-owned buffer (passed into handle() by the
// processor); only the length comes back here. The handler never owns
// the reply storage, so there is no buffer-lifetime contract to honor.
struct CommandReply {
    AckStatus status      = AckStatus::Ok;
    size_t    payload_len = 0;
};

// The single inbound-command handler. CommandProcessor frames a message
// off the TCP stream and calls handle(); this class owns the opcode
// switch and the protocol semantics behind each command. One handler
// for the whole command space: v3 has ~15 opcodes, too few to be worth
// a per-category split.
//
// Each handle_* method is a thin translator: parse the payload with
// WireReader, call the relevant AppContext object, optionally fill the
// reply buffer with WireWriter, return a CommandReply. The real logic
// lives in the AppContext-owned objects (Playback, AnimationStore,
// ...), not here.
//
// Liveness. Ping is a normal no-op handler. The processor reports
// "handled" on any successful frame; ControllerLink treats that as
// controller activity and resets its liveness deadline. There is no
// special Ping-evidence channel.
//
// Reboot. handle_reboot_ sets ctx.reboot_requested = true and returns
// {Ok, 0}. The App's outer loop reads the flag after the processor's
// poll() returns and calls ctx.system.reboot() once the ACK is on the
// wire. SystemPlatform is never called from here.
//
// Opcode map (opcode constants in src/link_protocol.h):
//   0x01 SetProfile           Persist the strip length to the
//                             profile_kv. If equal to the current
//                             profile, no-op; if different, persist
//                             and request reboot so the new geometry
//                             takes effect from a clean boot.
//   0x10 Load                 Decode a live program blob into Playback.
//   0x11 Start                Start a loaded program.
//   0x12 Jump                 Seek the live program.
//   0x13 Pause                Pause the live program.
//   0x14 Resume               Resume the live program.
//   0x15 Stop                 Stop the live program.
//   0x16 PlayLocalAnimation   Play the stored animation at a given
//                             play-order position, looping.
//   0x20 StoreAnimation       Store a blob under a name, overwriting
//                             that name's blob if it already exists.
//   0x21 EraseAnimation       Remove a stored animation by name.
//   0x22 SetAnimationOrder    Replace the play order (a permutation of
//                             the stored names).
//   0x30 Reboot               ACK, then App reboots after the ACK flush.
//   0x40 QueryDeviceStatus    Snapshot from ctx.status plus
//                             ctx.animations.count().
//   0x41 Ping                 No-op ACK; liveness happens via the
//                             processor's "handled" return.
//   0x42 QueryLocalAnimations Stored animations in play order; one
//                             {name, strip_length, crc32} record each.
//
// REGISTER (0x00) is NOT handled here: it is a one-shot outbound
// message the link sends during connect. Inbound 0x00 -> UnknownCommand.
// 0x02 is reserved (old SyncLease, gone; sync moved to UDP).

class CommandHandler {
public:
    explicit CommandHandler(AppContext& ctx);

    // The processor calls handle() with the framed opcode, the payload
    // slice it parsed off the wire, and the reply scratch it owns. The
    // handler fills up to reply_cap bytes via WireWriter and returns
    // payload_len in the CommandReply.
    CommandReply handle(uint8_t opcode,
                        const uint8_t* payload, size_t payload_len,
                        uint8_t* reply, size_t reply_cap);

private:
    CommandReply handle_set_profile_(WireReader& r);
    CommandReply handle_load_(WireReader& r);
    CommandReply handle_start_(WireReader& r);
    CommandReply handle_jump_(WireReader& r);
    CommandReply handle_pause_(WireReader& r);
    CommandReply handle_resume_(WireReader& r);
    CommandReply handle_stop_(WireReader& r);
    CommandReply handle_play_local_animation_(WireReader& r);
    CommandReply handle_store_animation_(WireReader& r,
                                         const uint8_t* payload,
                                         size_t payload_len);
    CommandReply handle_erase_animation_(WireReader& r);
    CommandReply handle_set_animation_order_(WireReader& r);
    CommandReply handle_reboot_(WireReader& r);
    CommandReply handle_query_device_status_(WireReader& r,
                                             uint8_t* reply, size_t reply_cap);
    CommandReply handle_ping_(WireReader& r);
    CommandReply handle_query_local_animations_(uint8_t* reply, size_t reply_cap);

    AppContext& _ctx;
};
