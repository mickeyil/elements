#pragma once

#include <cstddef>
#include <cstdint>

#include "platform/network_interface.h"
#include "platform/wifi_cred_store.h"

// Wi-Fi connection state machine for ESP32. Non-blocking; advances
// one step per poll() call through scan, rank, and connect phases.
//
// begin()   load credentials and start the first scan.
// poll()    advance the state machine; returns connection transition: unchanged / link_up / link_down.
// is_up()   true while connected.

class WifiManager
{
public:
    
    explicit WifiManager(WifiCredStore& creds);

    // Merge compiled credentials from secrets.h into the store (added if
    // missing, updated if the password changed). Call before deciding
    // whether the store is empty, so a compiled-in network keeps a fresh
    // board out of the provisioning portal.
    void merge_dev_credentials();

    // Start the first scan for known networks.
    void begin();

    // Advance the Wi-Fi state machine one step. Non-blocking.
    NetworkTransition poll();
    bool is_up() const { return _phase == ConnectionState::Connected; }

private:
    
    enum class ConnectionState : uint8_t {
        NotConnected,
        Scanning,
        Connecting,
        Connected,
    };

    struct APCandidate {
        size_t  cred_idx = 0;
        int32_t rssi = 0;
        int32_t channel = 0;
        bool    has_bssid = false;
        uint8_t bssid[6] = {};
    };

    // Scan visible APs and try each known network in order.
    void try_known_networks_();

    // Collect scan results and fallbacks into an ordered candidate list.
    void collect_candidates_(int16_t scan_count);

    void add_candidate_(size_t cred_idx, int32_t rssi, int32_t channel,
                      const uint8_t* bssid);
    bool find_candidate_(size_t cred_idx, size_t& out_idx) const;
    void sort_scanned_candidates_();

    // Launch the next usable candidate, or end the search.
    void try_next_candidate_();

    // Launch one connection attempt and arm its timeout.
    bool start_candidate_(const APCandidate& candidate);

    void end_network_search_();

    WifiCredStore& _creds;

    ConnectionState _phase = ConnectionState::NotConnected;
    APCandidate  _candidates[MAX_STORED_WIFI_CREDS] = {};
    size_t   _candidate_count = 0;
    size_t   _candidate_pos = 0;
    uint32_t _scan_started_ms = 0;      // meaningful only while scanning
    uint32_t _candidate_started_ms = 0;   // meaningful only while connecting
    uint32_t _last_search_ended_ms = 0;  // last failed network search or disconnect
};
