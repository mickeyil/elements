#pragma once

#include <cstddef>

#include "wifi_manager.h"

static constexpr firmware::DevWifiCredential DEV_WIFI_CREDENTIALS[] = {
    // {"example-ssid", "example-password"},
};

static constexpr size_t DEV_WIFI_CREDENTIAL_COUNT =
    sizeof(DEV_WIFI_CREDENTIALS) / sizeof(DEV_WIFI_CREDENTIALS[0]);
