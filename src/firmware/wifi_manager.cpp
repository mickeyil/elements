#include "wifi_manager.h"

#include <Arduino.h>

#include "diagnostics.h"
#include "wire_constants.h"

#if __has_include("secrets.h")
#include "secrets.h"
#else
#error "Missing src/firmware/secrets.h. Copy src/firmware/secrets.example.h and fill in local Wi-Fi credentials."
#endif

namespace firmware {
namespace {

void configure_wifi_runtime_()
{
    WiFi.mode(WIFI_STA);
    WiFi.persistent(false);
    WiFi.setAutoReconnect(true);
    WiFi.setSleep(false);
}

}  // namespace

void WifiManager::begin()
{
    _preferences_ready = _preferences.begin(kNvsNamespace, false);
    if (!_preferences_ready) {
        log_line("[wifi] failed to open preferences namespace=%s", kNvsNamespace);
    }

    load_last_good_ssid_();
    if (connect_to_dev_wifi_()) {
        _ready = true;
        _last_retry_ms = 0;
    }
}

WifiTransition WifiManager::poll()
{
    if (WiFi.status() == WL_CONNECTED) {
        if (!_ready) {
            _ready = true;
            _last_retry_ms = 0;
            return WifiTransition::connected;
        }
        return WifiTransition::none;
    }

    if (_ready) {
        log_line("[wifi] disconnected");
        _ready = false;
        return WifiTransition::disconnected;
    }

    const uint32_t now = millis();
    if (_last_retry_ms != 0 && now - _last_retry_ms < kWifiRetryIntervalMs) {
        return WifiTransition::none;
    }

    _last_retry_ms = now;
    if (connect_to_dev_wifi_()) {
        _ready = true;
        _last_retry_ms = 0;
        return WifiTransition::connected;
    }
    return WifiTransition::none;
}

bool WifiManager::is_ready() const
{
    return _ready;
}

WifiSnapshot WifiManager::snapshot() const
{
    WifiSnapshot snapshot;
    snapshot.ready = _ready;
    snapshot.preferences_ready = _preferences_ready;
    snapshot.last_good_ssid = _last_good_ssid;
    snapshot.local_ip = _ready ? WiFi.localIP() : IPAddress();
    snapshot.connect_attempts = _connect_attempts;
    snapshot.connect_successes = _connect_successes;
    return snapshot;
}

bool WifiManager::connect_to_dev_wifi_()
{
    configure_wifi_runtime_();

    for (size_t i = 0; i < DEV_WIFI_CREDENTIAL_COUNT; ++i) {
        const DevWifiCredential& cred = DEV_WIFI_CREDENTIALS[i];
        const bool preferred = _last_good_ssid.length() > 0 && _last_good_ssid == cred.ssid;
        if (!preferred) {
            continue;
        }

        _connect_attempts += 1;
        log_line("[wifi] connecting to %s (preferred)", cred.ssid);
        WiFi.disconnect(true, true);
        delay(100);
        WiFi.begin(cred.ssid, cred.password);

        const uint32_t started = millis();
        while (millis() - started < kWifiPreferredTimeoutMs) {
            if (WiFi.status() == WL_CONNECTED) {
                _connect_successes += 1;
                log_line("[wifi] connected to %s ip=%s", cred.ssid, WiFi.localIP().toString().c_str());
                store_last_good_ssid_(cred.ssid);
                return true;
            }
            delay(250);
        }

        log_line(
            "[wifi] failed to connect to %s after %lums",
            cred.ssid,
            static_cast<unsigned long>(millis() - started)
        );
        break;
    }

    for (size_t i = 0; i < DEV_WIFI_CREDENTIAL_COUNT; ++i) {
        const DevWifiCredential& cred = DEV_WIFI_CREDENTIALS[i];

        _connect_attempts += 1;
        log_line("[wifi] connecting to %s", cred.ssid);
        WiFi.disconnect(true, true);
        delay(100);
        WiFi.begin(cred.ssid, cred.password);

        const uint32_t started = millis();
        while (millis() - started < kWifiConnectTimeoutMs) {
            if (WiFi.status() == WL_CONNECTED) {
                _connect_successes += 1;
                log_line("[wifi] connected to %s ip=%s", cred.ssid, WiFi.localIP().toString().c_str());
                store_last_good_ssid_(cred.ssid);
                return true;
            }
            delay(250);
        }

        log_line(
            "[wifi] failed to connect to %s after %lums",
            cred.ssid,
            static_cast<unsigned long>(millis() - started)
        );
    }

    return false;
}

void WifiManager::load_last_good_ssid_()
{
    if (!_preferences_ready) {
        return;
    }

    _last_good_ssid = _preferences.getString(kNvsLastGoodSsidKey, "");
    if (_last_good_ssid.length() > 0) {
        log_line("[wifi] cached preferred ssid=%s", _last_good_ssid.c_str());
    }
}

void WifiManager::store_last_good_ssid_(const char* ssid)
{
    if (!_preferences_ready || ssid == nullptr || ssid[0] == '\0') {
        return;
    }
    if (_last_good_ssid == ssid) {
        return;
    }
    if (!_preferences.putString(kNvsLastGoodSsidKey, ssid)) {
        log_line("[wifi] failed to cache preferred ssid=%s", ssid);
        return;
    }
    _last_good_ssid = ssid;
    log_line("[wifi] cached preferred ssid=%s", _last_good_ssid.c_str());
}

}  // namespace firmware
