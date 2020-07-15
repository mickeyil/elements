#pragma once

#include <algorithm>
#include <cstdint>
#include <cstdio>
#ifdef ARDUINO
#include <Arduino.h>
#endif

#define MAX_DPRINTF_BUF  160

#ifdef DEBUG_HELPERS
#  ifdef ARDUINO
#    define DPRINTF(Format, ...) do {                                   \
       char dbuf[MAX_DPRINTF_BUF];                                     \
    snprintf(dbuf, MAX_DPRINTF_BUF, (Format), ## __VA_ARGS__);      \
    Serial.println(dbuf);                                           \
  } while(0);
#else
#define DPRINTF(Format, ...) do {                                   \
    char dbuf[MAX_DPRINTF_BUF];                                     \
    snprintf(dbuf, MAX_DPRINTF_BUF, (Format), ## __VA_ARGS__);      \
    printf("%s\n",dbuf);                                           \
  } while(0);
#endif
#else
#define DPRINTF
#endif

static const char * arr2str(uint8_t *arr, unsigned int len, bool hex=false)
{
  static char sbuf[MAX_DPRINTF_BUF];
  unsigned int index = 0;
  unsigned maxlen = std::min(MAX_DPRINTF_BUF / 4 - 1, (int) len);
  for (unsigned int i = 0; i < maxlen; i++) {
    if (hex) {
      index += sprintf(&sbuf[index], "%02X ", arr[i]);
    } else {
      index += sprintf(&sbuf[index], "%03d ", arr[i]);
    }
  }
  return sbuf;
}
