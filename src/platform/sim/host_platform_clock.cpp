#include "platform/platform_clock.h"

#include <chrono>

int64_t now_us()
{
    // Anchor to process start so the value means "time since boot" like
    // the ESP build; steady_clock's raw epoch is the host's boot, which
    // would leak the machine's uptime into log stamps. A sim reboot is a
    // real re-exec, so the anchor resets exactly when a device would.
    static const auto t0 = std::chrono::steady_clock::now();
    return std::chrono::duration_cast<std::chrono::microseconds>(
               std::chrono::steady_clock::now() - t0)
        .count();
}
