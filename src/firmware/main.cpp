#include <Arduino.h>
#include <Esp.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <esp_system.h>

#include <cstring>
#include <memory>

#include "diagnostics.h"
#include "esp_device.h"
#include "runtime_state.h"
#include "tcp_commands.h"
#include "wifi_runtime.h"
#include "wire_constants.h"

#if __has_include("secrets.h")
#include "secrets.h"
#else
#error "Missing src/firmware/secrets.h. Copy src/firmware/secrets.example.h and fill in local Wi-Fi credentials."
#endif

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

uint32_t make_boot_token()
{
    uint32_t token = esp_random();
    if (token == 0) {
        token = 1;
    }
    return token;
}

String make_device_uid()
{
    const uint64_t chip_id = ESP.getEfuseMac();
    const uint8_t mac0 = static_cast<uint8_t>((chip_id >> 0) & 0xff);
    const uint8_t mac1 = static_cast<uint8_t>((chip_id >> 8) & 0xff);
    const uint8_t mac2 = static_cast<uint8_t>((chip_id >> 16) & 0xff);
    const uint8_t mac3 = static_cast<uint8_t>((chip_id >> 24) & 0xff);
    const uint8_t mac4 = static_cast<uint8_t>((chip_id >> 32) & 0xff);
    const uint8_t mac5 = static_cast<uint8_t>((chip_id >> 40) & 0xff);
    char buf[32];
    snprintf(
        buf,
        sizeof(buf),
        "esp32-%02x%02x%02x%02x%02x%02x",
        mac0,
        mac1,
        mac2,
        mac3,
        mac4,
        mac5
    );
    return String(buf);
}

void clear_runtime_connection_state()
{
    g_link.configured = false;
    g_transport.device_id = 0;
    g_transport.frame_port = 0;
    g_transport.controller_ip = IPAddress();
    g_link.tcp_buf_used = 0;
    g_link.last_sync_seq = 0;
    if (g_device) {
        g_device->clear_sync();
    }
}

void disconnect_controller(const char* reason = nullptr)
{
    if (g_link.connected) {
        g_diag.tcp_disconnect_count += 1;
        if (reason && reason[0] != '\0') {
            log_line("[tcp] controller disconnected: %s", reason);
        } else {
            log_line("[tcp] controller disconnected");
        }
    }

    if (g_tcp_client) {
        g_tcp_client.stop();
    }
    g_link.connected = false;
    clear_runtime_connection_state();
}

void handle_network_down()
{
    disconnect_controller("network down");
}

void send_discovery_hello()
{
    const size_t uid_len = g_runtime.device_uid.length();
    if (uid_len == 0 || uid_len > 255) {
        return;
    }

    uint8_t pkt[5 + 255];
    memcpy(pkt, &kDiscoveryMagic, 2);
    memcpy(pkt + 2, &kTcpPort, 2);
    pkt[4] = static_cast<uint8_t>(uid_len);
    memcpy(pkt + 5, g_runtime.device_uid.c_str(), uid_len);

    const IPAddress broadcast_ip(255, 255, 255, 255);
    if (g_discovery_udp.beginPacket(broadcast_ip, kDiscoveryPort)) {
        g_discovery_udp.write(pkt, 5 + uid_len);
        if (g_discovery_udp.endPacket() != 0) {
            g_diag.hello_count += 1;
        }
    }
}

void handle_sync_request(const uint8_t* packet, size_t packet_size, IPAddress sender_ip, uint16_t sender_port)
{
    if (packet_size != 15 || packet[0] != kSyncReq) {
        return;
    }

    uint16_t seq = 0;
    int64_t t1_us = 0;
    memcpy(&seq, packet + 1, 2);
    memcpy(&t1_us, packet + 7, 8);

    const int64_t t2_us = esp_timer_get_time();
    uint8_t resp[31];
    resp[0] = kSyncResp;
    memcpy(resp + 1, &seq, 2);
    memcpy(resp + 3, &g_runtime.boot_token, 4);
    memcpy(resp + 7, &t1_us, 8);
    memcpy(resp + 15, &t2_us, 8);
    const int64_t t3_us = esp_timer_get_time();
    memcpy(resp + 23, &t3_us, 8);

    if (g_discovery_udp.beginPacket(sender_ip, sender_port)) {
        g_discovery_udp.write(resp, sizeof(resp));
        g_discovery_udp.endPacket();
    }
}

void poll_discovery_udp()
{
    while (true) {
        const int packet_size = g_discovery_udp.parsePacket();
        if (packet_size <= 0) {
            return;
        }

        uint8_t buf[64];
        const int n = g_discovery_udp.read(buf, sizeof(buf));
        if (n <= 0) {
            continue;
        }

        const IPAddress sender_ip = g_discovery_udp.remoteIP();
        const uint16_t sender_port = g_discovery_udp.remotePort();

        if (buf[0] == kSyncReq && n >= 15) {
            handle_sync_request(buf, static_cast<size_t>(n), sender_ip, sender_port);
            continue;
        }

        if (n != 4) {
            continue;
        }

        uint16_t magic = 0;
        memcpy(&magic, buf, sizeof(magic));
        if (magic != kDiscoveryMagic) {
            continue;
        }
        if (buf[2] != kDiscoveryTypeReject || buf[3] != kDiscoveryReasonDuplicateUid) {
            continue;
        }

        g_wifi.duplicate_uid_rejected = true;
        log_line("[discovery] controller rejected duplicate uid=%s", g_runtime.device_uid.c_str());
        return;
    }
}

void maybe_send_hello()
{
    if (!g_wifi.server_started || !g_wifi.ready || g_wifi.duplicate_uid_rejected) {
        return;
    }

    const uint32_t now = millis();
    if (g_diag.last_hello_ms == 0 || now - g_diag.last_hello_ms >= kHelloIntervalMs) {
        send_discovery_hello();
        g_diag.last_hello_ms = now;
    }

}

void accept_controller()
{
    if (!g_wifi.server_started) {
        return;
    }

    WiFiClient incoming = g_tcp_server.available();
    if (!incoming) {
        return;
    }

    if (g_tcp_client && g_tcp_client.connected()) {
        log_line(
            "[tcp] rejecting extra controller connection from %s",
            incoming.remoteIP().toString().c_str()
        );
        incoming.stop();
        return;
    }

    g_tcp_client = incoming;
    g_tcp_client.setNoDelay(true);
    clear_runtime_connection_state();
    g_link.connected = true;
    g_diag.tcp_accept_count += 1;
    log_line("[tcp] controller connected: %s", g_tcp_client.remoteIP().toString().c_str());
}

void handle_disconnect()
{
    if (!g_link.connected || g_tcp_client.connected()) {
        return;
    }
    disconnect_controller();
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
    disconnect_controller("reboot");
    delay(20);
    ESP.restart();
}

int poll_tcp_commands()
{
    if (!(g_tcp_client && g_tcp_client.connected())) {
        return -1;
    }

    TcpCommandContext ctx{g_device, g_transport, g_link, g_diag, g_runtime};

    while (g_tcp_client.available() > 0) {
        if (g_link.tcp_buf.size() - g_link.tcp_buf_used < 512) {
            g_link.tcp_buf.resize(g_link.tcp_buf.size() * 2);
        }

        const int n = g_tcp_client.read(
            g_link.tcp_buf.data() + g_link.tcp_buf_used,
            g_link.tcp_buf.size() - g_link.tcp_buf_used
        );
        if (n < 0) {
            return -1;
        }
        if (n == 0) {
            break;
        }
        g_link.tcp_buf_used += static_cast<size_t>(n);
    }

    while (g_link.tcp_buf_used >= 4) {
        uint32_t msg_len = 0;
        memcpy(&msg_len, g_link.tcp_buf.data(), sizeof(msg_len));

        if (msg_len < 1) {
            g_link.tcp_buf_used -= 4;
            if (g_link.tcp_buf_used > 0) {
                memmove(g_link.tcp_buf.data(), g_link.tcp_buf.data() + 4, g_link.tcp_buf_used);
            }
            continue;
        }

        if (msg_len > kTcpMsgMax) {
            log_line("[tcp] message too large (%lu), dropping client", static_cast<unsigned long>(msg_len));
            return -1;
        }

        const size_t total = 4 + static_cast<size_t>(msg_len);
        if (g_link.tcp_buf_used < total) {
            if (g_link.tcp_buf.size() < total) {
                g_link.tcp_buf.resize(total);
            }
            break;
        }

        const uint8_t cmd_type = g_link.tcp_buf[4];
        const uint8_t* payload = g_link.tcp_buf.data() + 5;
        const uint32_t payload_len = msg_len - 1;

        switch (cmd_type) {
            case kCmdConfigure:
                handle_cmd_configure(ctx, g_tcp_client, payload, payload_len);
                break;

            case kCmdSyncResult:
                handle_cmd_sync_result(ctx, payload, payload_len);
                break;

            case kCmdLoad:
                handle_cmd_load(ctx, g_tcp_client, payload, payload_len);
                break;

            case kCmdStart:
                handle_cmd_start(ctx, payload, payload_len);
                break;

            case kCmdJump:
                handle_cmd_jump(ctx, payload, payload_len);
                break;

            case kCmdPause:
                handle_cmd_pause(ctx);
                break;

            case kCmdResume:
                handle_cmd_resume(ctx, payload, payload_len);
                break;

            case kCmdStop:
                handle_cmd_stop(ctx);
                break;

            case kCmdReboot:
                handle_cmd_reboot(ctx, g_tcp_client);
                break;

            case kCmdDebugSeek:
            case kCmdDebugStep:
                log_line("[tcp] ignored debug-only command 0x%02x", cmd_type);
                break;

            default:
                log_line("[tcp] ignored unknown command 0x%02x", cmd_type);
                break;
        }

        g_link.tcp_buf_used -= total;
        if (g_link.tcp_buf_used > 0) {
            memmove(g_link.tcp_buf.data(), g_link.tcp_buf.data() + total, g_link.tcp_buf_used);
        }
    }

    return 0;
}

void send_frames()
{
    if (!g_link.configured || !g_device || g_transport.frame_port == 0) {
        return;
    }

    auto frames = g_device->drain_frames();
    for (auto& frame : frames) {
        uint8_t header[12];
        memcpy(header, &g_transport.device_id, 2);
        memcpy(header + 2, &frame.gen, 2);
        memcpy(header + 4, &frame.frame_index, 4);
        memcpy(header + 8, &frame.t_rel, 4);

        if (!g_frame_udp.beginPacket(g_transport.controller_ip, g_transport.frame_port)) {
            continue;
        }
        g_frame_udp.write(header, sizeof(header));
        if (!frame.rgb.empty()) {
            g_frame_udp.write(frame.rgb.data(), frame.rgb.size());
        }
        if (g_frame_udp.endPacket() != 0) {
            g_diag.frames_sent += 1;
            g_diag.have_frame_stats = true;
            g_diag.last_frame_gen = frame.gen;
            g_diag.last_frame_index = frame.frame_index;
            g_diag.last_frame_t_rel = frame.t_rel;
        }
    }
}

}  // namespace

void setup()
{
    Serial.begin(115200);
    delay(200);

    esp_device_init_leds();
    g_runtime.device_uid = make_device_uid();
    g_runtime.boot_token = make_boot_token();

    log_line("[boot] elements esp32 runtime starting");
    log_line("[boot] build=%s %s", __DATE__, __TIME__);
    log_line("[boot] uid=%s", g_runtime.device_uid.c_str());
    log_line("[boot] boot_token=%lu", static_cast<unsigned long>(g_runtime.boot_token));
    g_wifi.preferences_ready = g_preferences.begin(kNvsNamespace, false);
    if (!g_wifi.preferences_ready) {
        log_line("[wifi] failed to open preferences namespace=%s", kNvsNamespace);
    }
    wifi_load_last_good_ssid(g_wifi, g_preferences);

    wifi_connect_to_dev_wifi(g_wifi, g_diag, g_preferences);
    wifi_ensure_network_services_started(
        g_wifi,
        g_diag,
        g_tcp_server,
        g_discovery_udp,
        g_runtime.device_uid
    );
}

void loop()
{
    wifi_ensure_connected(
        g_wifi,
        g_diag,
        g_preferences,
        g_tcp_server,
        g_discovery_udp,
        g_runtime.device_uid,
        handle_network_down
    );
    wifi_ensure_network_services_started(
        g_wifi,
        g_diag,
        g_tcp_server,
        g_discovery_udp,
        g_runtime.device_uid
    );
    accept_controller();

    if (g_tcp_client && g_tcp_client.connected()) {
        if (poll_tcp_commands() < 0) {
            disconnect_controller("socket error");
        }
    } else {
        handle_disconnect();
    }

    if (g_link.configured && g_device) {
        g_device->tick_once();
        send_frames();
    }

    maybe_send_hello();
    if (g_wifi.server_started) {
        poll_discovery_udp();
    }
    maybe_log_status(g_diag, g_wifi, g_link);
    maybe_reboot();
    delay(kLoopDelayMs);
}
