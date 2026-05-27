#pragma once

#include <cstddef>
#include <cstdint>

#include "network_interface.h"
#include "wifi_cred_store.h"

// ESP-side Wi-Fi handler driven from NetworkInterface::poll(). Holds
// the scan and connect state machine so the App loop never blocks on
// association.
//
// A sweep scans visible APs, tries known SSIDs by signal strength,
// then falls back to last_ssid and the remaining saved credentials.

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
    enum class Phase : uint8_t {
        Idle,
        Scanning,
        Connecting,
    };

    struct Attempt {
        size_t  cred_idx = 0;
        int32_t rssi = 0;
        int32_t channel = 0;
        bool    has_bssid = false;
        uint8_t bssid[6] = {};
    };

    // Start a sweep with an async scan.
    void start_sweep_();

    // Convert scan results to connection candidates.
    void build_attempts_(int16_t scan_count);

    // Add hidden or currently-unseen credentials after scanned ones.
    void add_fallback_attempts_();

    void add_attempt_(size_t cred_idx, int32_t rssi, int32_t channel,
                      const uint8_t* bssid);
    bool find_attempt_(size_t cred_idx, size_t& out_idx) const;
    bool find_cred_(const char* ssid, size_t& out_idx) const;
    void sort_scanned_attempts_();

    // Launch the next usable candidate, or finish the sweep.
    void try_next_attempt_();

    // Launch one association attempt and arm its timeout.
    bool start_attempt_(const Attempt& attempt);

    void finish_sweep_();

    WifiCredStore& _creds;

    bool     _is_up = false;
    Phase    _phase = Phase::Idle;
    Attempt  _attempts[MAX_STORED_WIFI_CREDS] = {};
    size_t   _attempt_count = 0;
    size_t   _attempt_pos = 0;
    uint32_t _scan_started_ms = 0;      // meaningful only while scanning
    uint32_t _attempt_started_ms = 0;   // meaningful only while connecting
    uint32_t _last_sweep_ended_ms = 0;  // last failed sweep or disconnect
};
