#pragma once

#include <cstddef>

#include "app/wifi_cred_store.h"

static constexpr WifiCredential DEV_WIFI_CREDENTIALS[] = {
    // {"example-ssid", "example-password"},
};

static constexpr size_t DEV_WIFI_CREDENTIAL_COUNT =
    sizeof(DEV_WIFI_CREDENTIALS) / sizeof(DEV_WIFI_CREDENTIALS[0]);
