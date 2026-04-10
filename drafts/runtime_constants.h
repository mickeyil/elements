#pragma once

// Draft-only shared sentinel constants used across multiple runtime headers.

#include <cstdint>

// Optional PixelView reference sentinel.
static constexpr uint16_t PIXV_NONE = 0xFFFF;

// Optional PixelBufferPool buffer reference sentinel.
static constexpr uint16_t PIXBUF_NONE = 0xFFFF;
