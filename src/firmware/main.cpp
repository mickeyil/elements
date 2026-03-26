#include <Arduino.h>
#include <Esp.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiUdp.h>

#include <memory>

#include "controller_runtime.h"
#include "device_identity.h"
#include "discovery_runtime.h"
#include "diagnostics.h"
#include "esp_device.h"
#include "runtime_state.h"
#include "wifi_runtime.h"
#include "wire_constants.h"

namespace {
using namespace firmware;

WiFiServer g_tcp_server(kTcpPort);
WiFiClient g_tcp_client;
WiFiUDP g_discovery_udp;
WiFiUDP g_frame_udp;
Preferences g_preferences;

std::unique_ptr<ESPDevice> g_device;
TransportState g_transport;
WifiState g_wifi;
ControllerLinkState g_link(kTcpBufInitial);
DiagnosticsState g_diag;
RuntimeState g_runtime;

ControllerRuntimeContext make_controller_context()
{
    return ControllerRuntimeContext{
        g_tcp_server,
        g_tcp_client,
        g_frame_udp,
        g_device,
        g_transport,
        g_wifi,
        g_link,
        g_diag,
        g_runtime,
    };
}

void handle_network_down()
{
    auto ctx = make_controller_context();
    controller_disconnect(ctx, "network down");
}

void maybe_reboot()
{
    if (!g_runtime.reboot_pending) {
        return;
    }

    const uint32_t now = millis();
    if (static_cast<int32_t>(now - g_runtime.reboot_deadline_ms) < 0) {
        return;
    }

    log_line("[sys] rebooting now");
    if (g_tcp_client) {
        g_tcp_client.flush();
        delay(20);
    }
    auto ctx = make_controller_context();
    controller_disconnect(ctx, "reboot");
    delay(20);
    ESP.restart();
}

}  // namespace

void setup()
{
    Serial.begin(115200);
    delay(200);

    // Initialize the local device identity before any networked work starts.
    esp_device_init_leds();
    g_runtime.device_uid = make_device_uid();
    g_runtime.boot_token = make_boot_token();

    // Emit boot details early so reconnect and provisioning issues are visible.
    log_line("[boot] elements esp32 runtime starting");
    log_line("[boot] build=%s %s", __DATE__, __TIME__);
    log_line("[boot] uid=%s", g_runtime.device_uid.c_str());
    log_line("[boot] boot_token=%lu", static_cast<unsigned long>(g_runtime.boot_token));

    // Restore Wi-Fi preferences and bring up the controller-facing endpoints.
    g_wifi.preferences_ready = g_preferences.begin(kNvsNamespace, false);
    if (!g_wifi.preferences_ready) {
        log_line("[wifi] failed to open preferences namespace=%s", kNvsNamespace);
    }
    wifi_load_last_good_ssid(g_wifi, g_preferences);

    wifi_connect_to_dev_wifi(g_wifi, g_diag, g_preferences);
    wifi_ensure_network_services_started(
        g_wifi, g_diag, g_tcp_server, g_discovery_udp, g_runtime.device_uid);
}

void loop()
{
    auto controller_ctx = make_controller_context();

    // Keep Wi-Fi up and the TCP/UDP service endpoints bound.
    wifi_ensure_connected(
        g_wifi, g_diag, g_preferences, g_tcp_server,
        g_discovery_udp, g_runtime.device_uid, handle_network_down);
    wifi_ensure_network_services_started(
        g_wifi, g_diag, g_tcp_server, g_discovery_udp, g_runtime.device_uid);

    // Service controller commands before advancing playback.
    controller_accept(controller_ctx);

    if (g_tcp_client && g_tcp_client.connected()) {
        if (controller_poll_tcp_commands(controller_ctx) < 0) {
            controller_disconnect(controller_ctx, "socket error");
        }
    } else {
        controller_handle_disconnect(controller_ctx);
    }

    // Advance playback and flush any generated frames back to the controller.
    if (g_link.configured && g_device) {
        g_device->tick_once();
        controller_send_frames(controller_ctx);
    }

    // Handle discovery, sync, periodic status, and deferred reboot work.
    discovery_maybe_send_hello(g_discovery_udp, g_wifi, g_diag, g_runtime);
    if (g_wifi.server_started) {
        discovery_poll_udp(g_discovery_udp, g_wifi, g_diag, g_runtime);
    }
    maybe_log_status(g_diag, g_wifi, g_link);
    maybe_reboot();
    delay(kLoopDelayMs);
}
