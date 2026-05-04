#pragma once

#include <cstddef>
#include <cstdint>

// Wire constants for the controller-link TCP protocol.
//
// Opcodes are organized by high nibble (cmd >> 4). Each high nibble is
// a category owned by exactly one handler; see drafts/controller_link.md.
//
// Discovery (HELLO, OFFER, REJECT) and clock-sync (PING, PONG)
// constants are NOT here; they belong with their respective service
// modules. Sync constants live in src/clock_sync_client.cpp; discovery
// constants will land with the v3 discovery rewrite.

// Wire protocol generation. Bumped on every breaking wire change; sent
// in DEVICE_HELLO so the controller can refuse devices it doesn't
// understand.
constexpr uint8_t PROTOCOL_VERSION = 3;

// Fixed-size UID slot on the wire. ASCII, null-padded if shorter.
// Format: "esp-XXXXXXXXXXXX" (12 hex chars from MAC) for ESP devices,
// "sim-..........." (developer-supplied) for sim devices. Parser
// rule: trim at first \0, require trailing bytes are also \0,
// require trimmed content to be printable.
//
// In-memory the uid lives in a UID_CAPACITY-byte C-string buffer (see
// device_identity.h); the wire slot is exactly UID_WIRE_SIZE bytes.
constexpr size_t UID_WIRE_SIZE = 16;

// ---- Session (0x0_) -------------------------------------------------------

// Sent device -> controller as the first TCP message after connect.
// Never seen inbound by the device; the parser ACKs UnknownCommand if
// it ever is.
constexpr uint8_t CMD_DEVICE_HELLO = 0x00;

constexpr uint8_t CMD_SET_PROFILE  = 0x01;

// 0x02 is reserved (was SyncLease in an earlier v3 draft). Sync is now
// device-initiated UDP on its own port, not a TCP opcode. See
// drafts/synced_clock.md.

// ---- Playback (0x1_) ------------------------------------------------------

constexpr uint8_t CMD_LOAD   = 0x10;
constexpr uint8_t CMD_START  = 0x11;
constexpr uint8_t CMD_JUMP   = 0x12;
constexpr uint8_t CMD_PAUSE  = 0x13;
constexpr uint8_t CMD_RESUME = 0x14;
constexpr uint8_t CMD_STOP   = 0x15;

// ---- Storage (0x2_) -------------------------------------------------------

constexpr uint8_t CMD_STORE_BACKGROUND = 0x20;
constexpr uint8_t CMD_CLEAR_BACKGROUND = 0x21;

// ---- System (0x3_) --------------------------------------------------------

constexpr uint8_t CMD_REBOOT = 0x30;

// ---- Status (0x4_) --------------------------------------------------------

constexpr uint8_t CMD_QUERY_DEVICE_STATUS = 0x40;

// ---- Reply (0x8_, both directions) ----------------------------------------

constexpr uint8_t CMD_ACK = 0x80;

// ---- Buffering ------------------------------------------------------------

constexpr size_t TCP_BUF_INITIAL = 4096;
constexpr size_t TCP_MSG_MAX     = 256 * 1024;

// Notes on what was here in v2 (src/firmware/wire_constants.h) and is
// not here now:
//   - kTcpPort = 6053. v3 device side learns the TCP port from each
//     OFFER; no compile-time constant needed on the device.
//   - kCmdSetProfile = 0x05, kCmdAttach = 0x06, kSyncReq/Resp/Result
//     (0x01/0x02/0x03 in v2 over the same TCP stream). Replaced by
//     CMD_SET_PROFILE = 0x01 above plus device-initiated UDP sync.
//   - kCmdStoreBackground = 0x16, kCmdClearBackground = 0x17,
//     kCmdQueryDeviceStatus = 0x18. Now in the 0x2_ / 0x4_ ranges.
//   - kCmdDebugSeek/kCmdDebugStep. Dropped; debug commands are not
//     part of the v3 wire surface.
//   - Wi-Fi timeouts, NVS keys, hello/status intervals, loop and
//     reboot delays. Runtime/firmware concerns; will reappear in the
//     v3 firmware app or its dependencies, not in the wire header.
//   - Discovery magic / type / reason bytes will reappear in the v3
//     discovery code when it lands.
