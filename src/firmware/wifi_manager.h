#pragma once

#include <cstddef>
#include <cstdint>

#include "network_interface.h"

class WifiCredStore;

// ESP-side Wi-Fi handler driven from NetworkInterface::poll(). Holds
// the connect-attempt state machine and the credential walk so the
// App loop never blocks on association.
//
// Same-SSID reconnect is left to the supplicant (setAutoReconnect).
// The credential walk recovers "moved to a different known SSID."

class WifiManager {
public:
    explicit WifiManager(WifiCredStore& creds);

    // Reconcile the cred store against compiled DEV_WIFI_CREDENTIALS,
    // then kick the first attempt.
    void begin();

    NetworkTransition poll();
    bool is_up() const { return _is_up; }

private:
    void start_attempt_(const char* ssid, const char* password);
    void advance_walk_();
    bool load_attempt_at_walk_(char* ssid_out, char* pwd_out);

    WifiCredStore& _creds;

    bool     _is_up = false;
    bool     _walking = false;
    bool     _tried_last = false;
    uint32_t _attempt_started_ms = 0;
    uint32_t _last_scan_ended_ms = 0;
    size_t   _walk_idx = 0;
};
