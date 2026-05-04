#include <Arduino.h>

#include "esp_device_identity.h"
#include "firmware_app.h"

namespace {
FirmwareApp g_app;
}  // namespace

void setup()
{
    g_app.begin(make_esp32_device_identity());
}

void loop()
{
    g_app.run_once();
}
