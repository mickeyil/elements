#pragma once

#include <cstddef>
#include <cstdint>

#include "hardware_profile.h"  // MAX_STRIP_PIXELS

// Upper bounds on the structural counts and sizes accepted from a binary
// program blob. A decoder consults these to reject blobs that exceed the
// device's resource budget — too many layers, too many copy ops, oversized
// parameter blocks — before any allocation work happens.
static constexpr uint8_t  MAX_LAYER_COUNT      = 32;
static constexpr uint16_t MAX_BUFFER_COUNT     = 256;
static constexpr uint16_t MAX_PIXEL_VIEW_COUNT = 512;
static constexpr uint16_t MAX_COPY_OP_COUNT    = 512;
static constexpr uint16_t MAX_EVENTS_PER_LAYER = 1024;
static constexpr uint16_t MAX_PARAMS_BYTES     = 4096;
static constexpr size_t   MAX_POOL_BYTES       = 100 * 1024;
