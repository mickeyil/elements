#include "platform/esp32/esp_system_platform.h"

#include <Esp.h>

void EspSystemPlatform::reboot()
{
    // Does not return; the chip resets immediately.
    ESP.restart();
}
