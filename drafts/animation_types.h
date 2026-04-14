#pragma once

// Draft-only.
//
// Animation type IDs encoded in blob format v3. Used by the decoder to
// dispatch to the per-animation from_blob() factory and by each anim_*.h
// header to declare its own type.

#include <cstdint>

enum class AnimType : uint8_t {
    Wave  = 0,
    Shift = 1,
    Spark = 2,
    Paint = 3,
};
