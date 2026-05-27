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

// Delay before starting another credential sweep.
constexpr uint32_t WIFI_SWEEP_RETRY_INTERVAL_MS = 30'000;

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
        _sweeping = false;

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
        _sweeping = false;
        _last_sweep_ended_ms = millis();
        return NetworkTransition::went_down;
    }

    const uint32_t now = millis();

    if (_sweeping) {
        if (now - _attempt_started_ms < WIFI_CONNECT_ATTEMPT_TIMEOUT_MS) {
            return NetworkTransition::none;
        }
        advance_attempt_();
        if (!try_next_attempt_()) {
            Serial.println("[wifi] sweep exhausted");
        }
        return NetworkTransition::none;
    }

    // Idle. After the retry interval, start a fresh sweep.
    if (now - _last_sweep_ended_ms >= WIFI_SWEEP_RETRY_INTERVAL_MS) {
        start_sweep_();
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

void WifiManager::start_sweep_()
{
    _sweeping = true;
    _tried_last = false;
    _attempt_idx = 0;
    try_next_attempt_();
}

bool WifiManager::try_next_attempt_()
{
    char ssid[WIFI_SSID_BUF_SIZE];
    char password[WIFI_PASSWORD_BUF_SIZE];
    if (load_current_attempt_(ssid, password)) {
        start_attempt_(ssid, password);
        return true;
    }
    _sweeping = false;
    _last_sweep_ended_ms = millis();
    return false;
}

void WifiManager::advance_attempt_()
{
    if (!_tried_last) {
        // Restart the indexed sweep; the loader skips the slot
        // matching last_ssid since we already tried it.
        _tried_last = true;
        _attempt_idx = 0;
        return;
    }
    ++_attempt_idx;
}

bool WifiManager::load_current_attempt_(char* ssid_out, char* pwd_out)
{
    // First attempt of a sweep: try last_ssid if known.
    if (!_tried_last) {
        char last[WIFI_SSID_BUF_SIZE];
        if (_creds.last_ssid(last, sizeof(last)) &&
            _creds.get(last, pwd_out, WIFI_PASSWORD_BUF_SIZE)) {
            std::strncpy(ssid_out, last, WIFI_SSID_BUF_SIZE - 1);
            ssid_out[WIFI_SSID_BUF_SIZE - 1] = '\0';
            return true;
        }
        // No usable last_ssid; fall through to the indexed sweep.
        _tried_last = true;
        _attempt_idx = 0;
    }

    char last[WIFI_SSID_BUF_SIZE];
    const bool have_last = _creds.last_ssid(last, sizeof(last));

    while (_attempt_idx < _creds.count()) {
        if (!_creds.ssid_at(_attempt_idx, ssid_out, WIFI_SSID_BUF_SIZE)) {
            ++_attempt_idx;
            continue;
        }
        // Skip the last_ssid; we already tried it.
        if (have_last && std::strcmp(ssid_out, last) == 0) {
            ++_attempt_idx;
            continue;
        }
        if (_creds.get(ssid_out, pwd_out, WIFI_PASSWORD_BUF_SIZE)) {
            return true;
        }
        ++_attempt_idx;
    }
    return false;
}
