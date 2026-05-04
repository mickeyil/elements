#pragma once

#include <cstddef>
#include <cstdint>

// TCP controller-link protocol: opcodes, version, sizes.
//
// Opcodes are grouped by high nibble (cmd >> 4); each group is owned
// by one handler. See drafts/controller_link.md for the full spec.

// Bumped on every breaking wire change. Sent in DEVICE_HELLO.
constexpr uint8_t PROTOCOL_VERSION = 3;

// Fixed-size UID slot on the wire. ASCII, null-padded if shorter.
constexpr size_t UID_SIZE = 16;

// ---- Session (0x0_) -------------------------------------------------------

// First message the device sends after TCP connect.
constexpr uint8_t CMD_DEVICE_HELLO = 0x00;

constexpr uint8_t CMD_SET_PROFILE  = 0x01;

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
