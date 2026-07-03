#pragma once

#include <cstddef>
#include <cstdint>

// Device-side logging. Each call formats one line, echoes it to stdout
// (the ESP's UART, the sim's terminal) and buffers the record in a
// small static ring for LogSender to forward to the controller over
// UDP. Loss is acceptable by design: when the ring wraps, the oldest
// unsent record is overwritten and the controller reports the gap
// from the sequence numbers.
//
// Single-threaded like the rest of the device: call only from the main
// loop, never from an ISR or another task.

// Level byte; also the wire encoding (ASCII, readable in packet dumps).
constexpr uint8_t SLOG_LEVEL_INFO  = 'I';
constexpr uint8_t SLOG_LEVEL_WARN  = 'W';
constexpr uint8_t SLOG_LEVEL_ERROR = 'E';

// Formatted text cap per record, terminator included. Longer messages
// are truncated, not split.
constexpr size_t SLOG_TEXT_CAP = 224;

// Ring capacity in records (about 4 KB of static RAM).
constexpr size_t SLOG_RING_RECORDS = 16;

struct SlogRecord
{
    uint32_t seq       = 0;   // monotonically increasing from 1
    uint32_t uptime_ms = 0;   // device clock at log time
    uint8_t  level     = SLOG_LEVEL_INFO;
    char     text[SLOG_TEXT_CAP] = {};
};

void slog_info(const char* fmt, ...);
void slog_warn(const char* fmt, ...);
void slog_error(const char* fmt, ...);

// Sender interface: read the oldest buffered record without removing
// it; pop it once it is on the wire. peek returns false when empty.
bool slog_peek(SlogRecord& out);
void slog_pop();

// Test hook: forget buffered records and restart seq from 1.
void slog_reset();
