#pragma once

#include <cstddef>
#include <cstdint>

#include "network_interface.h"

class WifiCredStore;

// ESP-side Wi-Fi handler driven from NetworkInterface::poll(). Holds
// the connect-attempt state machine and the credential sweep so the
// App loop never blocks on association.
//
// A sweep is one pass through the credentials, trying each in turn
// until one connects or the list is exhausted. Same-SSID reconnect
// is left to the supplicant (setAutoReconnect). The sweep recovers
// "moved to a different known SSID."

class WifiManager {
public:
    explicit WifiManager(WifiCredStore& creds);

    // Reconcile the cred store against compiled DEV_WIFI_CREDENTIALS,
    // then start the first sweep.
    void begin();

    NetworkTransition poll();
    bool is_up() const { return _is_up; }

private:
    void start_attempt_(const char* ssid, const char* password);
    void start_sweep_();
    bool try_next_attempt_();
    void advance_attempt_();
    bool load_current_attempt_(char* ssid_out, char* pwd_out);

    WifiCredStore& _creds;

    bool     _is_up = false;
    bool     _sweeping = false;
    bool     _tried_last = false;
    uint32_t _attempt_started_ms = 0;
    uint32_t _last_sweep_ended_ms = 0;
    size_t   _attempt_idx = 0;
};
