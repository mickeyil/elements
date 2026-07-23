#include "platform/sim/sim_system_platform.h"

#include <cstdio>
#include <unistd.h>

void SimSystemPlatform::reboot()
{
    // Flush pending stdio logs (slog echoes via stdout) before the process
    // dies. This is not the ACK flush; the App loop already drained the
    // reboot ACK (bounded) before calling reboot().
    std::fflush(nullptr);
    _exit(SIM_REBOOT_EXIT_CODE);
}
