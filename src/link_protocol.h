#pragma once

#include <cstddef>
#include <cstdint>

// TCP controller-link protocol: opcodes, version, ACK status, sizes.
// See drafts/controller_link.md for the wire spec and
// src/command_handler.cpp for opcode behavior.

// Bumped on every breaking wire change. Sent in REGISTER.
constexpr uint8_t PROTOCOL_VERSION = 3;

// ---- Session / configuration ----------------------------------------------

// First message the device sends after TCP connect.
constexpr uint8_t CMD_REGISTER    = 0x00;
constexpr uint8_t CMD_SET_PROFILE = 0x01;

// ---- Playback -------------------------------------------------------------

constexpr uint8_t CMD_LOAD                 = 0x10;
constexpr uint8_t CMD_START                = 0x11;
constexpr uint8_t CMD_JUMP                 = 0x12;
constexpr uint8_t CMD_PAUSE                = 0x13;
constexpr uint8_t CMD_RESUME               = 0x14;
constexpr uint8_t CMD_STOP                 = 0x15;
constexpr uint8_t CMD_PLAY_LOCAL_ANIMATION = 0x16;

// ---- Local animation storage ----------------------------------------------

constexpr uint8_t CMD_STORE_ANIMATION     = 0x20;
constexpr uint8_t CMD_ERASE_ANIMATION     = 0x21;
constexpr uint8_t CMD_SET_ANIMATION_ORDER = 0x22;

// ---- System ---------------------------------------------------------------

constexpr uint8_t CMD_REBOOT = 0x30;

// ---- Status ---------------------------------------------------------------

constexpr uint8_t CMD_QUERY_DEVICE_STATUS   = 0x40;
constexpr uint8_t CMD_PING                  = 0x41;
constexpr uint8_t CMD_QUERY_LOCAL_ANIMATIONS = 0x42;

// ---- Reply (both directions) ----------------------------------------------

constexpr uint8_t CMD_ACK = 0x80;

// ACK status byte. Mirror of AckStatus in src/command_handler.h, kept
// as plain constants so non-C++ tooling can read them.
constexpr uint8_t ACK_OK               = 0;
constexpr uint8_t ACK_ERROR            = 1;
constexpr uint8_t ACK_WRONG_STATE      = 2;
constexpr uint8_t ACK_PROFILE_MISMATCH = 3;
constexpr uint8_t ACK_BAD_PAYLOAD      = 4;
constexpr uint8_t ACK_UNKNOWN_COMMAND  = 5;
constexpr uint8_t ACK_UNSYNCED         = 6;

// ---- Device status --------------------------------------------------------

// Device mode byte in the QueryDeviceStatus ACK. Mirror of DeviceMode
// in src/device_status.h, kept as plain constants so non-C++ tooling
// can read them.
constexpr uint8_t MODE_ATTACHED_CONTROLLED = 0;
constexpr uint8_t MODE_DETACHED_GRACE_HOLD = 1;
constexpr uint8_t MODE_DETACHED_BLANK      = 2;
constexpr uint8_t MODE_DETACHED_BACKGROUND = 3;

// ---- Timing ---------------------------------------------------------------

// How often the controller sends Ping during quiet stretches. Devices
// derive their liveness deadline from it.
constexpr int64_t PING_INTERVAL_MS = 5'000;

// ---- Sizes ----------------------------------------------------------------

// Shared cap for any program blob, live (LOAD) or stored (StoreAnimation).
// Drives TCP_MSG_MAX. Relax only if a real animation hits the ceiling.
constexpr size_t MAX_BLOB_BYTES = 16 * 1024;

// Largest inbound TCP message. The bound is StoreAnimation: 32-byte name
// slot + blob + 5 bytes of message header (length u32 + opcode u8). The
// slack absorbs the header and any future small additions. Sizes the
// CommandProcessor's fixed RX buffer.
constexpr size_t TCP_MSG_MAX = MAX_BLOB_BYTES + 256;
