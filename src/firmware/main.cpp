#include <Arduino.h>

#include "firmware_app.h"

namespace {
firmware::FirmwareApp g_app;
}  // namespace

void setup()
{
    g_app.begin();
}

void loop()
{
    g_app.run_once();
}
