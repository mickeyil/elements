#pragma once

// Draft-only.
//
// Validation caps for blob format v3. Enforced by the firmware decoder and
// mirrored by the Python compiler. See blob_format.md for the role of each
// cap and decoder.md for the compiler-side enforcement contract.

#include <cstddef>
#include <cstdint>

#include "hardware_profile.h"  // MAX_STRIP_PIXELS

static constexpr uint8_t  kMaxLayerCount     = 32;
static constexpr uint16_t kMaxBufferCount    = 256;
static constexpr uint16_t kMaxPixelViewCount = 512;
static constexpr uint16_t kMaxCopyOpCount    = 512;
static constexpr uint16_t kMaxEventsPerLayer = 1024;
static constexpr uint16_t kMaxParamsBytes    = 4096;
static constexpr size_t   kMaxPoolBytes      = 100 * 1024;
