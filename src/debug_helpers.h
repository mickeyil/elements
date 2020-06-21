#pragma once

#include <cassert>
#include <cerrno>
#include <cstdint>
#include <cstdio>
#include <ctime>
#include <sys/time.h>

template <typename T>
static void print_indexed_triplet(unsigned int idx, T a, T b, T c)
{
  assert(0);  // not implemented
}

template<>
void print_indexed_triplet<uint8_t>(unsigned int idx, uint8_t a, uint8_t b, uint8_t c)
{
  printf("%4u:%4u%4u%4u\n", idx, a, b, c);
}


static void print_pa_title(const char *a, const char *b, const char *c)
{
  printf("%-6s %-6s|%6s%6s%6s\n", "idx", "sidx", a, b, c);
  printf("%-6s+%6s+%6s%6s%6s\n", "======", "======", "======", "======", "======");
}


static void print_pa_values(unsigned int idx, unsigned int sidx, float a, float b, float c)
{
  printf("%6u:%6u:%6.1f%6.1f%6.1f\n", idx, sidx, a, b, c);
}


static void print_title(const char *a, const char *b, const char *c)
{
  printf("%-4s|%4s%4s%4s\n", "idx", a, b, c);
  printf("%-4s+%4s%4s%4s\n", "====", "====", "====", "====");
}

static double get_time_lf()
{
  timeval cur_time;
  gettimeofday(&cur_time, NULL);
  double frac = cur_time.tv_usec / 1000000.0;
  double epoch_ts = static_cast<double>(cur_time.tv_sec) + frac;
  return epoch_ts;
}

static int msleep(long msec)
{
    struct timespec ts;
    int res;

    if (msec < 0)
    {
        errno = EINVAL;
        return -1;
    }

    ts.tv_sec = msec / 1000;
    ts.tv_nsec = (msec % 1000) * 1000000;

    do {
        res = nanosleep(&ts, &ts);
    } while (res && errno == EINTR);

    return res;
}

#define debug_print(fmt, ...) \
            do { if (DEBUG_HELPERS) fprintf(stderr, fmt, __VA_ARGS__); } while (0)
