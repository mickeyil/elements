#pragma once

#include <cstddef>
#include <cstdint>

#include "network_interface.h"

class WifiCredStore;

// ESP-side Wi-Fi handler. Wraps Arduino WiFi association behind a
// non-blocking poll() that the App calls each loop tick. All connect
// attempts are launched and timed out without ever spinning in
// delay(), so animation rendering is unaffected during association.
//
// State machine:
//   begin()          one-time setup. Seeds WifiCredStore from
//                    compiled DEV_WIFI_CREDENTIALS if the store is
//                    empty, configures the radio, and kicks the first
//                    association attempt (last_ssid if known, else
//                    the first stored credential).
//
//   poll() per tick  reads WiFi.status() and advances the state
//                    machine. Returns the transition observed this
//                    tick (came_up / went_down / none). On success,
//                    writes the SSID into the store as last_ssid.
//                    On failure of one attempt (per-attempt timeout
//                    elapsed), advances to the next credential. After
//                    the full walk fails, waits WIFI_SCAN_RETRY_MS
//                    before starting a fresh walk.
//
// Same-SSID reconnection is left to the supplicant
// (WiFi.setAutoReconnect(true)). The credential walk is the recovery
// for "moved to a different known network" only.

class WifiManager {
public:
    explicit WifiManager(WifiCredStore& creds);

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
