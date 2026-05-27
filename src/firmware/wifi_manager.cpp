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

// Timeout for one Wi-Fi association attempt.
constexpr uint32_t WIFI_CONNECT_ATTEMPT_TIMEOUT_MS = 12'000;

// Timeout for one async Wi-Fi scan.
constexpr uint32_t WIFI_SCAN_TIMEOUT_MS = 5'000;

// Delay before starting another credential sweep.
constexpr uint32_t WIFI_SWEEP_RETRY_INTERVAL_MS = 30'000;

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
    start_sweep_();
}

NetworkTransition WifiManager::poll()
{
    const bool connected = (WiFi.status() == WL_CONNECTED);

    if (connected) {
        if (_is_up) return NetworkTransition::none;

        _is_up = true;
        _phase = Phase::Idle;
        _attempt_count = 0;
        _attempt_pos = 0;

        const String current = WiFi.SSID();
        if (current.length() > 0) {
            _creds.set_last_ssid(current.c_str());
        }
        return NetworkTransition::came_up;
    }

    // Not connected.
    if (_is_up) {
        _is_up = false;
        // Arm the idle gate so auto-reconnect gets a window first.
        _phase = Phase::Idle;
        _attempt_count = 0;
        _attempt_pos = 0;
        _last_sweep_ended_ms = millis();
        return NetworkTransition::went_down;
    }

    const uint32_t now = millis();

    if (_phase == Phase::Scanning) {
        const int16_t scan_count = WiFi.scanComplete();
        if (scan_count == WIFI_SCAN_RUNNING) {
            if (now - _scan_started_ms < WIFI_SCAN_TIMEOUT_MS) {
                return NetworkTransition::none;
            }
        }

        // Failed or timed-out scans still try saved credentials.
        build_attempts_(scan_count == WIFI_SCAN_RUNNING ? 0 : scan_count);
        WiFi.scanDelete();
        try_next_attempt_();
        return NetworkTransition::none;
    }

    if (_phase == Phase::Connecting) {
        if (now - _attempt_started_ms < WIFI_CONNECT_ATTEMPT_TIMEOUT_MS) {
            return NetworkTransition::none;
        }
        ++_attempt_pos;
        try_next_attempt_();
        return NetworkTransition::none;
    }

    // Idle. After the retry interval, start a fresh sweep.
    if (now - _last_sweep_ended_ms >= WIFI_SWEEP_RETRY_INTERVAL_MS) {
        start_sweep_();
    }
    return NetworkTransition::none;
}

void WifiManager::start_sweep_()
{
    _attempt_count = 0;
    _attempt_pos = 0;

    const int16_t scan_state = WiFi.scanNetworks(true);
    if (scan_state == WIFI_SCAN_RUNNING) {
        _phase = Phase::Scanning;
        _scan_started_ms = millis();
        return;
    }

    build_attempts_(scan_state);
    WiFi.scanDelete();
    try_next_attempt_();
}

void WifiManager::build_attempts_(int16_t scan_count)
{
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

            size_t cred_idx = 0;
            if (ssid.length() == 0 || !find_cred_(ssid.c_str(), cred_idx)) {
                continue;
            }
            add_attempt_(cred_idx, rssi, channel, bssid);
        }
        sort_scanned_attempts_();
    }

    add_fallback_attempts_();
}

void WifiManager::add_fallback_attempts_()
{
    char ssid[WIFI_SSID_BUF_SIZE];
    size_t cred_idx = 0;
    if (_creds.last_ssid(ssid, sizeof(ssid)) && find_cred_(ssid, cred_idx)) {
        add_attempt_(cred_idx, 0, 0, nullptr);
    }

    const size_t count = _creds.count();
    for (size_t i = 0; i < count; ++i) {
        add_attempt_(i, 0, 0, nullptr);
    }
}

void WifiManager::add_attempt_(size_t cred_idx, int32_t rssi, int32_t channel,
                               const uint8_t* bssid)
{
    size_t existing = 0;
    if (find_attempt_(cred_idx, existing)) {
        if (bssid != nullptr && (!_attempts[existing].has_bssid ||
                                 rssi > _attempts[existing].rssi)) {
            _attempts[existing].rssi = rssi;
            _attempts[existing].channel = channel;
            _attempts[existing].has_bssid = true;
            std::memcpy(_attempts[existing].bssid, bssid,
                        sizeof(_attempts[existing].bssid));
        }
        return;
    }

    if (_attempt_count >= MAX_STORED_WIFI_CREDS) return;

    Attempt& attempt = _attempts[_attempt_count++];
    attempt.cred_idx = cred_idx;
    attempt.rssi = rssi;
    attempt.channel = channel;
    attempt.has_bssid = (bssid != nullptr);
    if (bssid != nullptr) {
        std::memcpy(attempt.bssid, bssid, sizeof(attempt.bssid));
    }
}

bool WifiManager::find_attempt_(size_t cred_idx, size_t& out_idx) const
{
    for (size_t i = 0; i < _attempt_count; ++i) {
        if (_attempts[i].cred_idx == cred_idx) {
            out_idx = i;
            return true;
        }
    }
    return false;
}

bool WifiManager::find_cred_(const char* ssid, size_t& out_idx) const
{
    char stored[WIFI_SSID_BUF_SIZE];
    const size_t count = _creds.count();
    for (size_t i = 0; i < count; ++i) {
        if (_creds.ssid_at(i, stored, sizeof(stored)) &&
            std::strcmp(stored, ssid) == 0) {
            out_idx = i;
            return true;
        }
    }
    return false;
}

void WifiManager::sort_scanned_attempts_()
{
    for (size_t i = 1; i < _attempt_count; ++i) {
        Attempt current = _attempts[i];
        size_t j = i;
        while (j > 0 && current.rssi > _attempts[j - 1].rssi) {
            _attempts[j] = _attempts[j - 1];
            --j;
        }
        _attempts[j] = current;
    }
}

void WifiManager::try_next_attempt_()
{
    while (_attempt_pos < _attempt_count) {
        if (start_attempt_(_attempts[_attempt_pos])) {
            return;
        }
        ++_attempt_pos;
    }
    finish_sweep_();
}

bool WifiManager::start_attempt_(const Attempt& attempt)
{
    char ssid[WIFI_SSID_BUF_SIZE];
    char password[WIFI_PASSWORD_BUF_SIZE];
    if (!_creds.ssid_at(attempt.cred_idx, ssid, sizeof(ssid)) ||
        !_creds.get(ssid, password, sizeof(password))) {
        return false;
    }

    WiFi.disconnect(true, true);
    if (attempt.has_bssid) {
        WiFi.begin(ssid, password, attempt.channel, attempt.bssid);
    } else {
        WiFi.begin(ssid, password);
    }
    _attempt_started_ms = millis();
    _phase = Phase::Connecting;
    return true;
}

void WifiManager::finish_sweep_()
{
    _phase = Phase::Idle;
    _attempt_count = 0;
    _attempt_pos = 0;
    _last_sweep_ended_ms = millis();
}
