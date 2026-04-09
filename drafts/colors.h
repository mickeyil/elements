#pragma once

// Draft note for changes to:
//   src/colors.h
//
// This file is not a replacement implementation. It records the compile-time
// layout assertions that the PixelBufferPool design relies on.

#include <type_traits>

#include "colors.h"

// Intended additions near hsva_t in src/colors.h:
//
// static_assert(sizeof(hsva_t) == 4 * sizeof(float),
//               "hsva_t must remain exactly 4 floats");
// static_assert(alignof(hsva_t) == alignof(float),
//               "hsva_t alignment changed unexpectedly");
// static_assert(std::is_standard_layout_v<hsva_t>,
//               "hsva_t must remain standard-layout");
// static_assert(std::is_trivially_copyable_v<hsva_t>,
//               "hsva_t must remain trivially copyable");
//
// Rationale:
// - PixelBufferPool stores only hsva_t values
// - pooled mode slices only on hsva_t element boundaries
// - memset-based clearing assumes hsva_t stays a plain value type

static_assert(sizeof(hsva_t) == 4 * sizeof(float),
              "hsva_t must remain exactly 4 floats");
static_assert(alignof(hsva_t) == alignof(float),
              "hsva_t alignment changed unexpectedly");
static_assert(std::is_standard_layout<hsva_t>::value,
              "hsva_t must remain standard-layout");
static_assert(std::is_trivially_copyable<hsva_t>::value,
              "hsva_t must remain trivially copyable");
