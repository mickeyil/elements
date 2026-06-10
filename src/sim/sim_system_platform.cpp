#include "sim_system_platform.h"

#include <cstdio>
#include <unistd.h>

void SimSystemPlatform::reboot()
{
    // Flush pending stdio logs (slogger writes via stdout/stderr) before the
    // process dies. This is not the ACK flush; the App loop already put the
    // reboot ACK on the wire before calling reboot().
    std::fflush(nullptr);
    _exit(SIM_REBOOT_EXIT_CODE);
}
