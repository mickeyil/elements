#pragma once

#include "system_platform.h"

// SystemPlatform for the firmware build. reboot() restarts the chip;
// the controller sees the TCP connection drop and a fresh REGISTER
// with a new boot_token. The sim counterpart is SimSystemPlatform.

class EspSystemPlatform : public SystemPlatform
{
public:
    void reboot() override;
};
