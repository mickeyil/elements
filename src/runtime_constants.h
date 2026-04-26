#pragma once

#include <cstdint>

// u16 values that mean "unset" for optional PixelView and PixelBufferPool
// indices. A field set to one of these is treated as having no entry.

static constexpr uint16_t PIXV_NONE   = 0xFFFF;
static constexpr uint16_t PIXBUF_NONE = 0xFFFF;
