#pragma once

#include <cstdint>

// Animation type IDs encoded in the blob. The decoder dispatches on this to
// the right `from_blob()` factory.

enum class AnimType : uint8_t {
    Wave     = 0,
    Shift    = 1,
    Spark    = 2,
    Paint    = 3,
    Pacifica = 4,
};
