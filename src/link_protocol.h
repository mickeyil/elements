#pragma once

#include <cstddef>
#include <cstdint>

// TCP controller-link protocol: opcodes, version, ACK status, sizes.
// See drafts/controller_link.md for the wire spec and
// drafts/command_handler.h for opcode behavior.

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

// ACK status byte. Mirror of AckStatus in drafts/command_handler.h --
// the enum lives there because handlers produce it; this is the wire
// constant so non-C++ tooling can read it without a C++ header.
constexpr uint8_t ACK_OK               = 0;
constexpr uint8_t ACK_ERROR            = 1;
constexpr uint8_t ACK_WRONG_STATE      = 2;
constexpr uint8_t ACK_PROFILE_MISMATCH = 3;
constexpr uint8_t ACK_BAD_PAYLOAD      = 4;
constexpr uint8_t ACK_UNKNOWN_COMMAND  = 5;
constexpr uint8_t ACK_UNSYNCED         = 6;

// ---- Buffering ------------------------------------------------------------

constexpr size_t TCP_BUF_INITIAL = 4096;
constexpr size_t TCP_MSG_MAX     = 256 * 1024;
