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

// Matches the supplicant's own retry window; long enough to surface
// a real association failure before moving on.
constexpr uint32_t WIFI_CONNECT_ATTEMPT_TIMEOUT_MS = 12'000;

// Long enough to let auto-reconnect recover the same SSID, and to
// keep a stranded device from beating on the radio between scans.
constexpr uint32_t WIFI_SCAN_RETRY_INTERVAL_MS = 30'000;

void configure_wifi_runtime_()
{
    WiFi.mode(WIFI_STA);
    WiFi.persistent(false);
    WiFi.setAutoReconnect(true);
    WiFi.setSleep(false);
}

}  // namespace

WifiManager::WifiManager(WifiCredStore& creds) : _creds(creds) {}

void WifiManager::begin()
{
    if (_creds.empty() && DEV_WIFI_CREDENTIAL_COUNT > 0) {
        _creds.seed_from(DEV_WIFI_CREDENTIALS, DEV_WIFI_CREDENTIAL_COUNT);
    }

    configure_wifi_runtime_();

    _walking = true;
    _tried_last = false;
    _walk_idx = 0;

    char ssid[WIFI_SSID_BUF_SIZE];
    char password[WIFI_PASSWORD_BUF_SIZE];
    if (load_attempt_at_walk_(ssid, password)) {
        start_attempt_(ssid, password);
    } else {
        _walking = false;
        _last_scan_ended_ms = millis();
    }
}

NetworkTransition WifiManager::poll()
{
    const bool connected = (WiFi.status() == WL_CONNECTED);

    if (connected) {
        if (_is_up) return NetworkTransition::none;

        _is_up = true;
        _walking = false;

        const String current = WiFi.SSID();
        if (current.length() > 0) {
            _creds.set_last_ssid(current.c_str());
            Serial.printf("[wifi] connected ssid=%s ip=%s\n",
                          current.c_str(),
                          WiFi.localIP().toString().c_str());
        }
        return NetworkTransition::came_up;
    }

    // Not connected.
    if (_is_up) {
        _is_up = false;
        Serial.println("[wifi] disconnected");
        // Arm the idle gate so auto-reconnect gets a window first.
        _walking = false;
        _last_scan_ended_ms = millis();
        return NetworkTransition::went_down;
    }

    const uint32_t now = millis();

    if (_walking) {
        if (now - _attempt_started_ms < WIFI_CONNECT_ATTEMPT_TIMEOUT_MS) {
            return NetworkTransition::none;
        }
        // Attempt timed out. Advance to the next credential.
        advance_walk_();
        char ssid[WIFI_SSID_BUF_SIZE];
        char password[WIFI_PASSWORD_BUF_SIZE];
        if (load_attempt_at_walk_(ssid, password)) {
            start_attempt_(ssid, password);
        } else {
            _walking = false;
            _last_scan_ended_ms = now;
            Serial.println("[wifi] scan walk exhausted");
        }
        return NetworkTransition::none;
    }

    // Idle. After the scan-retry interval, start a fresh walk.
    if (now - _last_scan_ended_ms >= WIFI_SCAN_RETRY_INTERVAL_MS) {
        _walking = true;
        _tried_last = false;
        _walk_idx = 0;
        char ssid[WIFI_SSID_BUF_SIZE];
        char password[WIFI_PASSWORD_BUF_SIZE];
        if (load_attempt_at_walk_(ssid, password)) {
            start_attempt_(ssid, password);
        } else {
            _walking = false;
            _last_scan_ended_ms = now;
        }
    }
    return NetworkTransition::none;
}

void WifiManager::start_attempt_(const char* ssid, const char* password)
{
    Serial.printf("[wifi] attempt ssid=%s\n", ssid);
    WiFi.disconnect(true, true);
    WiFi.begin(ssid, password);
    _attempt_started_ms = millis();
}

void WifiManager::advance_walk_()
{
    if (!_tried_last) {
        // Restart the indexed walk; the loader skips the slot
        // matching last_ssid since we already tried it.
        _tried_last = true;
        _walk_idx = 0;
        return;
    }
    ++_walk_idx;
}

bool WifiManager::load_attempt_at_walk_(char* ssid_out, char* pwd_out)
{
    // First attempt of a walk: try last_ssid if known.
    if (!_tried_last) {
        char last[WIFI_SSID_BUF_SIZE];
        if (_creds.last_ssid(last, sizeof(last)) &&
            _creds.get(last, pwd_out, WIFI_PASSWORD_BUF_SIZE)) {
            std::strncpy(ssid_out, last, WIFI_SSID_BUF_SIZE - 1);
            ssid_out[WIFI_SSID_BUF_SIZE - 1] = '\0';
            return true;
        }
        // No usable last_ssid; fall through to the indexed walk.
        _tried_last = true;
        _walk_idx = 0;
    }

    char last[WIFI_SSID_BUF_SIZE];
    const bool have_last = _creds.last_ssid(last, sizeof(last));

    while (_walk_idx < _creds.count()) {
        if (!_creds.ssid_at(_walk_idx, ssid_out, WIFI_SSID_BUF_SIZE)) {
            ++_walk_idx;
            continue;
        }
        // Skip the last_ssid; we already tried it.
        if (have_last && std::strcmp(ssid_out, last) == 0) {
            ++_walk_idx;
            continue;
        }
        if (_creds.get(ssid_out, pwd_out, WIFI_PASSWORD_BUF_SIZE)) {
            return true;
        }
        ++_walk_idx;
    }
    return false;
}
