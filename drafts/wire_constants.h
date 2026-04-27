#pragma once

#include <cstddef>
#include <cstdint>

// Wire constants for the controller-link TCP protocol.
//
// Opcodes are organized by high nibble (cmd >> 4). Each high nibble is a
// category owned by exactly one handler; see controller_link.md.
//
// Discovery (HELLO, sync ping) constants are NOT here -- they belong with
// DiscoveryService.

namespace controller_link {

// TCP server port the device listens on.
constexpr uint16_t TCP_PORT = 6053;

// ---- Inbound opcodes -------------------------------------------------------

// Session (0x0_)
constexpr uint8_t CMD_SET_PROFILE = 0x00;
constexpr uint8_t CMD_ATTACH      = 0x01;
constexpr uint8_t CMD_SYNC_LEASE  = 0x02;

// Playback (0x1_)
constexpr uint8_t CMD_LOAD   = 0x10;
constexpr uint8_t CMD_START  = 0x11;
constexpr uint8_t CMD_JUMP   = 0x12;
constexpr uint8_t CMD_PAUSE  = 0x13;
constexpr uint8_t CMD_RESUME = 0x14;
constexpr uint8_t CMD_STOP   = 0x15;

// Storage (0x2_)
constexpr uint8_t CMD_STORE_BACKGROUND = 0x20;
constexpr uint8_t CMD_CLEAR_BACKGROUND = 0x21;

// System (0x3_)
constexpr uint8_t CMD_REBOOT = 0x30;

// Status (0x4_)
constexpr uint8_t CMD_QUERY_DEVICE_STATUS = 0x40;

// ---- Outbound opcodes ------------------------------------------------------

constexpr uint8_t CMD_ACK = 0x80;

// ---- Buffering -------------------------------------------------------------

// Initial inbound buffer size; grows up to TCP_MSG_MAX as larger blobs
// arrive (LOAD payloads can be sizable).
constexpr size_t TCP_BUF_INITIAL = 4096;
constexpr size_t TCP_MSG_MAX     = 256 * 1024;

// TODO: confirm 256 KiB upper bound is enough for the largest realistic
// LOAD payload after compiler v3. v2 ran fine at this cap.

}  // namespace controller_link
