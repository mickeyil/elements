#include "controller/slog.h"

#include <cstdarg>
#include <cstdio>

#include "platform/platform_clock.h"

namespace {

SlogRecord g_ring[SLOG_RING_RECORDS];
size_t   g_head     = 0;   // index of the oldest record
size_t   g_count    = 0;
uint32_t g_next_seq = 1;

void slog_append(uint8_t level, const char* fmt, va_list ap)
{
    size_t slot;
    if (g_count == SLOG_RING_RECORDS) {
        slot = g_head;                              // overwrite oldest
        g_head = (g_head + 1) % SLOG_RING_RECORDS;
    } else {
        slot = (g_head + g_count) % SLOG_RING_RECORDS;
        ++g_count;
    }

    SlogRecord& rec = g_ring[slot];
    rec.seq = g_next_seq++;
    rec.uptime_ms = static_cast<uint32_t>(now_us() / 1000);
    rec.level = level;
    std::vsnprintf(rec.text, sizeof(rec.text), fmt, ap);

    // Local echo: uptime-stamped so it needs no wall clock; the
    // controller stamps arrival time on its side.
    std::printf("[+%u.%03u] -%c- : %s\n",
                rec.uptime_ms / 1000, rec.uptime_ms % 1000,
                static_cast<char>(level), rec.text);
    std::fflush(stdout);
}

}  // namespace

void slog_info(const char* fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    slog_append(SLOG_LEVEL_INFO, fmt, ap);
    va_end(ap);
}

void slog_warn(const char* fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    slog_append(SLOG_LEVEL_WARN, fmt, ap);
    va_end(ap);
}

void slog_error(const char* fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    slog_append(SLOG_LEVEL_ERROR, fmt, ap);
    va_end(ap);
}

bool slog_peek(SlogRecord& out)
{
    if (g_count == 0) return false;
    out = g_ring[g_head];
    return true;
}

void slog_pop()
{
    if (g_count == 0) return;
    g_head = (g_head + 1) % SLOG_RING_RECORDS;
    --g_count;
}

void slog_reset()
{
    g_head = 0;
    g_count = 0;
    g_next_seq = 1;
}
