#pragma once

#include <Preferences.h>
#include <WiFi.h>

#include <cstdint>

namespace firmware {

enum class WifiTransition {
    none,
    connected,
    disconnected,
};

struct WifiSnapshot {
    bool ready = false;
    bool preferences_ready = false;
    String last_good_ssid;
    IPAddress local_ip;
    uint32_t connect_attempts = 0;
    uint32_t connect_successes = 0;
};

class WifiManager {
public:
    void begin();
    WifiTransition poll();
    bool is_ready() const;
    WifiSnapshot snapshot() const;

private:
    bool connect_to_dev_wifi_();
    void load_last_good_ssid_();
    void store_last_good_ssid_(const char* ssid);

    Preferences _preferences;
    bool _ready = false;
    bool _preferences_ready = false;
    uint32_t _last_retry_ms = 0;
    String _last_good_ssid;
    uint32_t _connect_attempts = 0;
    uint32_t _connect_successes = 0;
};

}  // namespace firmware
