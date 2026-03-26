#include "wifi_runtime.h"

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

void store_last_good_ssid(WifiState& wifi, Preferences& preferences, const char* ssid)
{
    if (!wifi.preferences_ready || ssid == nullptr || ssid[0] == '\0') {
        return;
    }
    if (wifi.last_good_ssid == ssid) {
        return;
    }
    if (!preferences.putString(kNvsLastGoodSsidKey, ssid)) {
        log_line("[wifi] failed to cache preferred ssid=%s", ssid);
        return;
    }
    wifi.last_good_ssid = ssid;
    log_line("[wifi] cached preferred ssid=%s", wifi.last_good_ssid.c_str());
}

}  // namespace

void wifi_load_last_good_ssid(WifiState& wifi, Preferences& preferences)
{
    if (!wifi.preferences_ready) {
        return;
    }
    wifi.last_good_ssid = preferences.getString(kNvsLastGoodSsidKey, "");
    if (wifi.last_good_ssid.length() > 0) {
        log_line("[wifi] cached preferred ssid=%s", wifi.last_good_ssid.c_str());
    }
}

bool wifi_connect_to_dev_wifi(
    WifiState& wifi,
    DiagnosticsState& diag,
    Preferences& preferences
)
{
    WiFi.mode(WIFI_STA);
    WiFi.persistent(false);
    WiFi.setAutoReconnect(true);
    WiFi.setSleep(false);

    auto try_credential = [&](const DevWifiCredential& cred, uint32_t timeout_ms, bool preferred) {
        diag.wifi_connect_attempts += 1;
        if (preferred) {
            log_line("[wifi] connecting to %s (preferred)", cred.ssid);
        } else {
            log_line("[wifi] connecting to %s", cred.ssid);
        }

        WiFi.disconnect(true, true);
        delay(100);
        WiFi.begin(cred.ssid, cred.password);

        const uint32_t started = millis();
        while (millis() - started < timeout_ms) {
            if (WiFi.status() == WL_CONNECTED) {
                diag.wifi_connect_successes += 1;
                log_line(
                    "[wifi] connected to %s ip=%s",
                    cred.ssid,
                    WiFi.localIP().toString().c_str()
                );
                store_last_good_ssid(wifi, preferences, cred.ssid);
                return true;
            }
            delay(250);
        }

        log_line(
            "[wifi] failed to connect to %s after %lums",
            cred.ssid,
            static_cast<unsigned long>(millis() - started)
        );
        return false;
    };

    if (wifi.last_good_ssid.length() > 0) {
        for (size_t i = 0; i < DEV_WIFI_CREDENTIAL_COUNT; ++i) {
            const auto& cred = DEV_WIFI_CREDENTIALS[i];
            if (wifi.last_good_ssid != cred.ssid) {
                continue;
            }
            if (try_credential(cred, kWifiPreferredTimeoutMs, true)) {
                return true;
            }
            break;
        }
    }

    for (size_t i = 0; i < DEV_WIFI_CREDENTIAL_COUNT; ++i) {
        const auto& cred = DEV_WIFI_CREDENTIALS[i];
        if (try_credential(cred, kWifiConnectTimeoutMs, false)) {
            return true;
        }
    }

    return false;
}

void wifi_stop_network_services(
    WifiState& wifi,
    WiFiServer& tcp_server,
    WiFiUDP& discovery_udp,
    NetworkDownCallback on_network_down
)
{
    if (on_network_down != nullptr) {
        on_network_down();
    }
    tcp_server.end();
    discovery_udp.stop();
    wifi.server_started = false;
}

void wifi_ensure_network_services_started(
    WifiState& wifi,
    DiagnosticsState& diag,
    WiFiServer& tcp_server,
    WiFiUDP& discovery_udp,
    const String& device_uid
)
{
    if (wifi.server_started || WiFi.status() != WL_CONNECTED) {
        return;
    }

    if (!discovery_udp.begin(kDiscoveryPort)) {
        log_line("[wifi] failed to bind discovery UDP port %u", kDiscoveryPort);
        return;
    }

    tcp_server.begin();
    tcp_server.setNoDelay(true);
    wifi.server_started = true;
    diag.last_hello_ms = 0;

    log_line(
        "[net] uid=%s ip=%s tcp=%u discovery=%u",
        device_uid.c_str(),
        WiFi.localIP().toString().c_str(),
        kTcpPort,
        kDiscoveryPort
    );
}

void wifi_ensure_connected(
    WifiState& wifi,
    DiagnosticsState& diag,
    Preferences& preferences,
    WiFiServer& tcp_server,
    WiFiUDP& discovery_udp,
    const String& device_uid,
    NetworkDownCallback on_network_down
)
{
    const wl_status_t status = WiFi.status();
    if (status == WL_CONNECTED) {
        if (!wifi.ready) {
            wifi.ready = true;
            wifi.last_retry_ms = 0;
            wifi_ensure_network_services_started(
                wifi,
                diag,
                tcp_server,
                discovery_udp,
                device_uid
            );
        }
        return;
    }

    if (wifi.ready) {
        log_line("[wifi] disconnected");
        wifi.ready = false;
        wifi_stop_network_services(wifi, tcp_server, discovery_udp, on_network_down);
    }

    const uint32_t now = millis();
    if (wifi.last_retry_ms != 0 && now - wifi.last_retry_ms < kWifiRetryIntervalMs) {
        return;
    }

    wifi.last_retry_ms = now;
    wifi_connect_to_dev_wifi(wifi, diag, preferences);
}

}  // namespace firmware
