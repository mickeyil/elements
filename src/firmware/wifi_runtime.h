#pragma once

#include <Preferences.h>
#include <WiFi.h>
#include <WiFiUdp.h>

#include "runtime_state.h"

namespace firmware {

using NetworkDownCallback = void (*)();

void wifi_load_last_good_ssid(WifiState& wifi, Preferences& preferences);

bool wifi_connect_to_dev_wifi(
    WifiState& wifi,
    DiagnosticsState& diag,
    Preferences& preferences
);

void wifi_stop_network_services(
    WifiState& wifi,
    WiFiServer& tcp_server,
    WiFiUDP& discovery_udp,
    NetworkDownCallback on_network_down
);

void wifi_ensure_network_services_started(
    WifiState& wifi,
    DiagnosticsState& diag,
    WiFiServer& tcp_server,
    WiFiUDP& discovery_udp,
    const String& device_uid
);

void wifi_ensure_connected(
    WifiState& wifi,
    DiagnosticsState& diag,
    Preferences& preferences,
    WiFiServer& tcp_server,
    WiFiUDP& discovery_udp,
    const String& device_uid,
    NetworkDownCallback on_network_down
);

}  // namespace firmware
