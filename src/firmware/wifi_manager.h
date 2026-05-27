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

    // Advance the Wi-Fi state machine without blocking. Returns only
    // the edge seen on this tick.
    NetworkTransition poll();
    bool is_up() const { return _is_up; }

private:
    // Launch one association attempt and arm its timeout.
    void start_attempt_(const char* ssid, const char* password);

    // Start a credential sweep: last_ssid first, then indexed creds.
    void start_sweep_();

    // Launch the current candidate. Returns false when the sweep is exhausted.
    bool try_next_attempt_();

    // Move past the timed-out candidate.
    void advance_attempt_();

    // Load the current candidate into caller buffers. Skips last_ssid
    // during the indexed part of a sweep.
    bool load_current_attempt_(char* ssid_out, char* pwd_out);

    WifiCredStore& _creds;

    bool     _is_up = false;
    bool     _sweeping = false;
    bool     _tried_last = false;
    uint32_t _attempt_started_ms = 0;   // meaningful only while _sweeping
    uint32_t _last_sweep_ended_ms = 0;  // last failed sweep or disconnect
    size_t   _attempt_idx = 0;
};
