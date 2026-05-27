#pragma once

// Edge seen on the most recent poll() call. `none` is the common case;
// the others fire exactly once on the tick the change is first observed.
enum class NetworkTransition {
    none,
    came_up,
    went_down,
};
