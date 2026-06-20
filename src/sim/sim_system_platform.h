#pragma once

#include "system_platform.h"

// Exit code the sim binary uses for a reboot request. The supervisor
// (elemctl sim) treats this code as "re-exec me"; any other code is a
// real exit. The Python supervisor mirrors this value independently; it
// cannot read this constant.
constexpr int SIM_REBOOT_EXIT_CODE = 64;

// SystemPlatform for the sim build. reboot() exits with the sentinel so
// the supervisor relaunches; RAM is wiped naturally and boot_token
// regenerates through the normal startup path. The firmware counterpart
// is EspSystemPlatform over ESP.restart().

class SimSystemPlatform : public SystemPlatform
{
public:
    void reboot() override;
};
