#include "wifi_manager.h"

#include <Arduino.h>
#include <WiFi.h>

#include <cstring>

#include "wifi_cred_store.h"

#if __has_include("secrets.h")
#include "secrets.h"
#else
static constexpr const WifiCredential* DEV_WIFI_CREDENTIALS = nullptr;
static constexpr size_t DEV_WIFI_CREDENTIAL_COUNT = 0;
#endif

namespace {

// Timeout for one Wi-Fi association candidate.
constexpr uint32_t WIFI_CONNECT_ATTEMPT_TIMEOUT_MS = 12'000;

// Timeout for one async Wi-Fi scan.
constexpr uint32_t WIFI_SCAN_TIMEOUT_MS = 5'000;

// Delay before starting another network search.
constexpr uint32_t WIFI_NETWORK_SEARCH_INTERVAL_MS = 30'000;

void configure_wifi_runtime_()
{
    WiFi.mode(WIFI_STA);          // Station mode; this device joins an AP.
    WiFi.persistent(false);       // Do not rewrite ESP SDK Wi-Fi flash config.
    WiFi.setAutoReconnect(true);  // Let the supplicant recover the same SSID.
    WiFi.setSleep(false);         // Keep latency predictable for control traffic.
}

}  // namespace

WifiManager::WifiManager(WifiCredStore& creds) : _creds(creds) {}

void WifiManager::begin()
{
    if (DEV_WIFI_CREDENTIAL_COUNT > 0) {
        _creds.merge_from(DEV_WIFI_CREDENTIALS, DEV_WIFI_CREDENTIAL_COUNT);
    }

    configure_wifi_runtime_();
    try_known_networks_();
}

NetworkTransition WifiManager::poll()
{
    const bool connected = (WiFi.status() == WL_CONNECTED);

    if (connected) {
        if (_phase == ConnectionState::Connected) return NetworkTransition::unchanged;

        _phase = ConnectionState::Connected;
        _candidate_count = 0;
        _candidate_pos = 0;

        const String current = WiFi.SSID();
        if (current.length() > 0) {
            _creds.set_last_ssid(current.c_str());
        }
        return NetworkTransition::link_up;
    }

    // Not connected.
    if (_phase == ConnectionState::Connected) {
        // Arm the retry gate so auto-reconnect gets a window first.
        _phase = ConnectionState::NotConnected;
        _candidate_count = 0;
        _candidate_pos = 0;
        _last_search_ended_ms = millis();
        return NetworkTransition::link_down;
    }

    const uint32_t now = millis();

    if (_phase == ConnectionState::Scanning) {
        const int16_t scan_count = WiFi.scanComplete();
        if (scan_count == WIFI_SCAN_RUNNING) {
            if (now - _scan_started_ms < WIFI_SCAN_TIMEOUT_MS) {
                return NetworkTransition::unchanged;
            }
        }

        // Failed or timed-out scans still try saved credentials.
        collect_candidates_(scan_count == WIFI_SCAN_RUNNING ? 0 : scan_count);
        WiFi.scanDelete();
        try_next_candidate_();
        return NetworkTransition::unchanged;
    }

    if (_phase == ConnectionState::Connecting) {
        if (now - _candidate_started_ms < WIFI_CONNECT_ATTEMPT_TIMEOUT_MS) {
            return NetworkTransition::unchanged;
        }
        ++_candidate_pos;
        try_next_candidate_();
        return NetworkTransition::unchanged;
    }

    // Not connected. After the retry interval, start a fresh network search.
    if (now - _last_search_ended_ms >= WIFI_NETWORK_SEARCH_INTERVAL_MS) {
        try_known_networks_();
    }
    return NetworkTransition::unchanged;
}

void WifiManager::try_known_networks_()
{
    _candidate_count = 0;
    _candidate_pos = 0;

    const int16_t scan_state = WiFi.scanNetworks(true);
    if (scan_state == WIFI_SCAN_RUNNING) {
        _phase = ConnectionState::Scanning;
        _scan_started_ms = millis();
        return;
    }

    collect_candidates_(scan_state);
    WiFi.scanDelete();
    try_next_candidate_();
}

void WifiManager::collect_candidates_(int16_t scan_count)
{
    size_t saved_idxs[MAX_STORED_WIFI_CREDS] = {};
    char saved_ssids[MAX_STORED_WIFI_CREDS][WIFI_SSID_BUF_SIZE] = {};
    size_t saved_count = 0;

    const size_t stored_count = _creds.count();
    for (size_t i = 0; i < stored_count && saved_count < MAX_STORED_WIFI_CREDS; ++i) {
        if (_creds.ssid_at(i, saved_ssids[saved_count], WIFI_SSID_BUF_SIZE)) {
            saved_idxs[saved_count] = i;
            ++saved_count;
        }
    }

    const auto find_saved = [&](const char* ssid, size_t& out_idx) {
        for (size_t i = 0; i < saved_count; ++i) {
            if (std::strcmp(saved_ssids[i], ssid) == 0) {
                out_idx = i;
                return true;
            }
        }
        return false;
    };

    if (scan_count > 0) {
        for (int16_t i = 0; i < scan_count; ++i) {
            String ssid;
            uint8_t encryption = 0;
            int32_t rssi = 0;
            uint8_t* bssid = nullptr;
            int32_t channel = 0;

            if (!WiFi.getNetworkInfo(static_cast<uint8_t>(i), ssid, encryption,
                                     rssi, bssid, channel)) {
                continue;
            }

            size_t saved_idx = 0;
            if (ssid.length() == 0 || !find_saved(ssid.c_str(), saved_idx)) {
                continue;
            }
            add_candidate_(saved_idxs[saved_idx], rssi, channel, bssid);
        }
        sort_scanned_candidates_();
    }

    char ssid[WIFI_SSID_BUF_SIZE];
    size_t saved_idx = 0;
    if (_creds.last_ssid(ssid, sizeof(ssid)) && find_saved(ssid, saved_idx)) {
        add_candidate_(saved_idxs[saved_idx], 0, 0, nullptr);
    }

    for (size_t i = 0; i < saved_count; ++i) {
        add_candidate_(saved_idxs[i], 0, 0, nullptr);
    }
}

void WifiManager::add_candidate_(size_t cred_idx, int32_t rssi, int32_t channel,
                               const uint8_t* bssid)
{
    size_t existing = 0;
    if (find_candidate_(cred_idx, existing)) {
        if (bssid != nullptr && (!_candidates[existing].has_bssid ||
                                 rssi > _candidates[existing].rssi)) {
            _candidates[existing].rssi = rssi;
            _candidates[existing].channel = channel;
            _candidates[existing].has_bssid = true;
            std::memcpy(_candidates[existing].bssid, bssid,
                        sizeof(_candidates[existing].bssid));
        }
        return;
    }

    if (_candidate_count >= MAX_STORED_WIFI_CREDS) return;

    APCandidate& candidate = _candidates[_candidate_count++];
    candidate.cred_idx = cred_idx;
    candidate.rssi = rssi;
    candidate.channel = channel;
    candidate.has_bssid = (bssid != nullptr);
    if (bssid != nullptr) {
        std::memcpy(candidate.bssid, bssid, sizeof(candidate.bssid));
    }
}

bool WifiManager::find_candidate_(size_t cred_idx, size_t& out_idx) const
{
    for (size_t i = 0; i < _candidate_count; ++i) {
        if (_candidates[i].cred_idx == cred_idx) {
            out_idx = i;
            return true;
        }
    }
    return false;
}

void WifiManager::sort_scanned_candidates_()
{
    for (size_t i = 1; i < _candidate_count; ++i) {
        APCandidate current = _candidates[i];
        size_t j = i;
        while (j > 0 && current.rssi > _candidates[j - 1].rssi) {
            _candidates[j] = _candidates[j - 1];
            --j;
        }
        _candidates[j] = current;
    }
}

void WifiManager::try_next_candidate_()
{
    while (_candidate_pos < _candidate_count) {
        if (start_candidate_(_candidates[_candidate_pos])) {
            return;
        }
        ++_candidate_pos;
    }
    end_network_search_();
}

bool WifiManager::start_candidate_(const APCandidate& candidate)
{
    char ssid[WIFI_SSID_BUF_SIZE];
    char password[WIFI_PASSWORD_BUF_SIZE];
    if (!_creds.ssid_at(candidate.cred_idx, ssid, sizeof(ssid)) ||
        !_creds.get(ssid, password, sizeof(password))) {
        return false;
    }

    WiFi.disconnect(true, true);
    if (candidate.has_bssid) {
        WiFi.begin(ssid, password, candidate.channel, candidate.bssid);
    } else {
        WiFi.begin(ssid, password);
    }
    _candidate_started_ms = millis();
    _phase = ConnectionState::Connecting;
    return true;
}

void WifiManager::end_network_search_()
{
    _phase = ConnectionState::NotConnected;
    _candidate_count = 0;
    _candidate_pos = 0;
    _last_search_ended_ms = millis();
}
