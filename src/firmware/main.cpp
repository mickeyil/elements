#include <Arduino.h>
#include <Esp.h>
#include <Preferences.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <esp_system.h>

#include <cstdarg>
#include <memory>
#include <vector>
#include <cstring>

#include "esp_device.h"

#if __has_include("secrets.h")
#include "secrets.h"
#else
#error "Missing src/firmware/secrets.h. Copy src/firmware/secrets.example.h and fill in local Wi-Fi credentials."
#endif

namespace {

constexpr uint16_t TCP_PORT = 6053;
constexpr uint16_t DISCOVERY_PORT = 6040;
constexpr uint16_t MAX_DEVICE_PIXELS = 250;

constexpr uint8_t CMD_CONFIGURE = 0x04;
constexpr uint8_t SYNC_REQ = 0x01;
constexpr uint8_t SYNC_RESP = 0x02;
constexpr uint8_t CMD_SYNC_RESULT = 0x03;
constexpr uint8_t CMD_LOAD = 0x10;
constexpr uint8_t CMD_START = 0x11;
constexpr uint8_t CMD_JUMP = 0x12;
constexpr uint8_t CMD_PAUSE = 0x13;
constexpr uint8_t CMD_RESUME = 0x14;
constexpr uint8_t CMD_STOP = 0x15;
constexpr uint8_t CMD_REBOOT = 0x30;
constexpr uint8_t CMD_DEBUG_SEEK = 0x22;
constexpr uint8_t CMD_DEBUG_STEP = 0x23;
constexpr uint8_t CMD_ACK = 0x80;

constexpr uint16_t DISCOVERY_MAGIC = 0x454C;
constexpr uint8_t DISCOVERY_TYPE_REJECT = 0x01;
constexpr uint8_t DISCOVERY_REASON_DUPLICATE_UID = 0x01;

constexpr size_t TCP_BUF_INITIAL = 4096;
constexpr size_t TCP_MSG_MAX = 256 * 1024;

constexpr uint32_t HELLO_INTERVAL_MS = 500;
constexpr uint32_t STATUS_INTERVAL_MS = 5000;
constexpr uint32_t WIFI_PREFERRED_TIMEOUT_MS = 4000;
constexpr uint32_t WIFI_RETRY_INTERVAL_MS = 5000;
constexpr uint32_t WIFI_CONNECT_TIMEOUT_MS = 12000;
constexpr uint32_t LOOP_DELAY_MS = 1;
constexpr uint32_t REBOOT_DELAY_MS = 100;
constexpr char NVS_NAMESPACE[] = "elements";
constexpr char NVS_LAST_GOOD_SSID_KEY[] = "last_ssid";

struct TransportState {
    uint16_t device_id = 0;
    uint16_t frame_port = 0;
    IPAddress controller_ip;
};

WiFiServer g_tcp_server(TCP_PORT);
WiFiClient g_tcp_client;
WiFiUDP g_discovery_udp;
WiFiUDP g_frame_udp;
Preferences g_preferences;

std::unique_ptr<ESPDevice> g_device;
TransportState g_transport;
std::vector<uint8_t> g_tcp_buf(TCP_BUF_INITIAL);
size_t g_tcp_buf_used = 0;

String g_device_uid;
bool g_wifi_ready = false;
bool g_server_started = false;
bool g_duplicate_uid_rejected = false;
bool g_configured = false;
bool g_controller_connected = false;
bool g_reboot_pending = false;
uint32_t g_boot_token = 0;
uint16_t g_last_sync_seq = 0;
uint32_t g_last_hello_ms = 0;
uint32_t g_last_status_ms = 0;
uint32_t g_last_wifi_retry_ms = 0;
uint32_t g_reboot_deadline_ms = 0;
uint32_t g_wifi_connect_attempts = 0;
uint32_t g_wifi_connect_successes = 0;
uint32_t g_hello_count = 0;
uint32_t g_tcp_accept_count = 0;
uint32_t g_tcp_disconnect_count = 0;
uint32_t g_configure_count = 0;
uint32_t g_load_count = 0;
uint32_t g_start_count = 0;
uint32_t g_jump_count = 0;
uint32_t g_pause_count = 0;
uint32_t g_resume_count = 0;
uint32_t g_stop_count = 0;
uint64_t g_frames_sent = 0;
bool g_have_frame_stats = false;
uint16_t g_last_frame_gen = 0;
uint32_t g_last_frame_index = 0;
float g_last_frame_t_rel = 0.0f;
bool g_preferences_ready = false;
String g_last_good_ssid;

const char* yes_no(bool value)
{
    return value ? "yes" : "no";
}

uint32_t make_boot_token()
{
    uint32_t token = esp_random();
    if (token == 0) {
        token = 1;
    }
    return token;
}

void log_line(const char* fmt, ...)
{
    char message[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(message, sizeof(message), fmt, ap);
    va_end(ap);
    Serial.println(message);
}

void load_last_good_ssid()
{
    if (!g_preferences_ready) {
        return;
    }
    g_last_good_ssid = g_preferences.getString(NVS_LAST_GOOD_SSID_KEY, "");
    if (g_last_good_ssid.length() > 0) {
        log_line("[wifi] cached preferred ssid=%s", g_last_good_ssid.c_str());
    }
}

void store_last_good_ssid(const char* ssid)
{
    if (!g_preferences_ready || ssid == nullptr || ssid[0] == '\0') {
        return;
    }
    if (g_last_good_ssid == ssid) {
        return;
    }
    if (!g_preferences.putString(NVS_LAST_GOOD_SSID_KEY, ssid)) {
        log_line("[wifi] failed to cache preferred ssid=%s", ssid);
        return;
    }
    g_last_good_ssid = ssid;
    log_line("[wifi] cached preferred ssid=%s", g_last_good_ssid.c_str());
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

bool send_all(WiFiClient& client, const uint8_t* data, size_t len)
{
    size_t offset = 0;
    while (offset < len) {
        const size_t written = client.write(data + offset, len - offset);
        if (written == 0) {
            return false;
        }
        offset += written;
    }
    return true;
}

void send_ack(WiFiClient& client, uint8_t status)
{
    uint8_t buf[6];
    const uint32_t len = 2;
    memcpy(buf, &len, sizeof(len));
    buf[4] = CMD_ACK;
    buf[5] = status;
    send_all(client, buf, sizeof(buf));
}

void clear_runtime_connection_state()
{
    g_configured = false;
    g_transport.device_id = 0;
    g_transport.frame_port = 0;
    g_transport.controller_ip = IPAddress();
    g_tcp_buf_used = 0;
    g_last_sync_seq = 0;
    if (g_device) {
        g_device->clear_sync();
    }
}

void disconnect_controller(const char* reason = nullptr)
{
    if (g_controller_connected) {
        g_tcp_disconnect_count += 1;
        if (reason && reason[0] != '\0') {
            log_line("[tcp] controller disconnected: %s", reason);
        } else {
            log_line("[tcp] controller disconnected");
        }
    }

    if (g_tcp_client) {
        g_tcp_client.stop();
    }
    g_controller_connected = false;
    clear_runtime_connection_state();
}

void stop_network_services()
{
    disconnect_controller("network down");
    g_tcp_server.end();
    g_discovery_udp.stop();
    g_server_started = false;
}

void ensure_network_services_started()
{
    if (g_server_started || WiFi.status() != WL_CONNECTED) {
        return;
    }

    if (!g_discovery_udp.begin(DISCOVERY_PORT)) {
        log_line("[wifi] failed to bind discovery UDP port %u", DISCOVERY_PORT);
        return;
    }

    g_tcp_server.begin();
    g_tcp_server.setNoDelay(true);
    g_server_started = true;
    g_last_hello_ms = 0;

    log_line(
        "[net] uid=%s ip=%s tcp=%u discovery=%u",
        g_device_uid.c_str(),
        WiFi.localIP().toString().c_str(),
        TCP_PORT,
        DISCOVERY_PORT
    );
}

bool connect_to_dev_wifi()
{
    WiFi.mode(WIFI_STA);
    WiFi.persistent(false);
    WiFi.setAutoReconnect(true);
    WiFi.setSleep(false);

    auto try_credential = [](const DevWifiCredential& cred, uint32_t timeout_ms, bool preferred) {
        g_wifi_connect_attempts += 1;
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
                g_wifi_connect_successes += 1;
                log_line(
                    "[wifi] connected to %s ip=%s",
                    cred.ssid,
                    WiFi.localIP().toString().c_str()
                );
                store_last_good_ssid(cred.ssid);
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

    if (g_last_good_ssid.length() > 0) {
        for (size_t i = 0; i < DEV_WIFI_CREDENTIAL_COUNT; ++i) {
            const auto& cred = DEV_WIFI_CREDENTIALS[i];
            if (g_last_good_ssid != cred.ssid) {
                continue;
            }
            if (try_credential(cred, WIFI_PREFERRED_TIMEOUT_MS, true)) {
                return true;
            }
            break;
        }
    }

    for (size_t i = 0; i < DEV_WIFI_CREDENTIAL_COUNT; ++i) {
        const auto& cred = DEV_WIFI_CREDENTIALS[i];
        if (try_credential(cred, WIFI_CONNECT_TIMEOUT_MS, false)) {
            return true;
        }
    }

    return false;
}

void ensure_wifi_connected()
{
    const wl_status_t status = WiFi.status();
    if (status == WL_CONNECTED) {
        if (!g_wifi_ready) {
            g_wifi_ready = true;
            g_last_wifi_retry_ms = 0;
            ensure_network_services_started();
        }
        return;
    }

    if (g_wifi_ready) {
        log_line("[wifi] disconnected");
        g_wifi_ready = false;
        stop_network_services();
    }

    const uint32_t now = millis();
    if (g_last_wifi_retry_ms != 0 && now - g_last_wifi_retry_ms < WIFI_RETRY_INTERVAL_MS) {
        return;
    }

    g_last_wifi_retry_ms = now;
    connect_to_dev_wifi();
}

void send_discovery_hello()
{
    const size_t uid_len = g_device_uid.length();
    if (uid_len == 0 || uid_len > 255) {
        return;
    }

    uint8_t pkt[5 + 255];
    memcpy(pkt, &DISCOVERY_MAGIC, 2);
    memcpy(pkt + 2, &TCP_PORT, 2);
    pkt[4] = static_cast<uint8_t>(uid_len);
    memcpy(pkt + 5, g_device_uid.c_str(), uid_len);

    const IPAddress broadcast_ip(255, 255, 255, 255);
    if (g_discovery_udp.beginPacket(broadcast_ip, DISCOVERY_PORT)) {
        g_discovery_udp.write(pkt, 5 + uid_len);
        if (g_discovery_udp.endPacket() != 0) {
            g_hello_count += 1;
        }
    }
}

void handle_sync_request(const uint8_t* packet, size_t packet_size, IPAddress sender_ip, uint16_t sender_port)
{
    if (packet_size != 15 || packet[0] != SYNC_REQ) {
        return;
    }

    uint16_t seq = 0;
    int64_t t1_us = 0;
    memcpy(&seq, packet + 1, 2);
    memcpy(&t1_us, packet + 7, 8);

    const int64_t t2_us = esp_timer_get_time();
    uint8_t resp[31];
    resp[0] = SYNC_RESP;
    memcpy(resp + 1, &seq, 2);
    memcpy(resp + 3, &g_boot_token, 4);
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

        if (buf[0] == SYNC_REQ && n >= 15) {
            handle_sync_request(buf, static_cast<size_t>(n), sender_ip, sender_port);
            continue;
        }

        if (n != 4) {
            continue;
        }

        uint16_t magic = 0;
        memcpy(&magic, buf, sizeof(magic));
        if (magic != DISCOVERY_MAGIC) {
            continue;
        }
        if (buf[2] != DISCOVERY_TYPE_REJECT || buf[3] != DISCOVERY_REASON_DUPLICATE_UID) {
            continue;
        }

        g_duplicate_uid_rejected = true;
        log_line("[discovery] controller rejected duplicate uid=%s", g_device_uid.c_str());
        return;
    }
}

void maybe_send_hello()
{
    if (!g_server_started || !g_wifi_ready || g_duplicate_uid_rejected) {
        return;
    }

    const uint32_t now = millis();
    if (g_last_hello_ms == 0 || now - g_last_hello_ms >= HELLO_INTERVAL_MS) {
        send_discovery_hello();
        g_last_hello_ms = now;
    }

}

void accept_controller()
{
    if (!g_server_started) {
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
    g_controller_connected = true;
    g_tcp_accept_count += 1;
    log_line("[tcp] controller connected: %s", g_tcp_client.remoteIP().toString().c_str());
}

void handle_disconnect()
{
    if (!g_controller_connected || g_tcp_client.connected()) {
        return;
    }
    disconnect_controller();
}

void maybe_reboot()
{
    if (!g_reboot_pending) {
        return;
    }

    const uint32_t now = millis();
    if (static_cast<int32_t>(now - g_reboot_deadline_ms) < 0) {
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

    while (g_tcp_client.available() > 0) {
        if (g_tcp_buf.size() - g_tcp_buf_used < 512) {
            g_tcp_buf.resize(g_tcp_buf.size() * 2);
        }

        const int n = g_tcp_client.read(
            g_tcp_buf.data() + g_tcp_buf_used,
            g_tcp_buf.size() - g_tcp_buf_used
        );
        if (n < 0) {
            return -1;
        }
        if (n == 0) {
            break;
        }
        g_tcp_buf_used += static_cast<size_t>(n);
    }

    while (g_tcp_buf_used >= 4) {
        uint32_t msg_len = 0;
        memcpy(&msg_len, g_tcp_buf.data(), sizeof(msg_len));

        if (msg_len < 1) {
            g_tcp_buf_used -= 4;
            if (g_tcp_buf_used > 0) {
                memmove(g_tcp_buf.data(), g_tcp_buf.data() + 4, g_tcp_buf_used);
            }
            continue;
        }

        if (msg_len > TCP_MSG_MAX) {
            log_line("[tcp] message too large (%lu), dropping client", static_cast<unsigned long>(msg_len));
            return -1;
        }

        const size_t total = 4 + static_cast<size_t>(msg_len);
        if (g_tcp_buf_used < total) {
            if (g_tcp_buf.size() < total) {
                g_tcp_buf.resize(total);
            }
            break;
        }

        const uint8_t cmd_type = g_tcp_buf[4];
        const uint8_t* payload = g_tcp_buf.data() + 5;
        const uint32_t payload_len = msg_len - 1;

        switch (cmd_type) {
            case CMD_CONFIGURE: {
                if (g_configured) {
                    send_ack(g_tcp_client, 1);
                    break;
                }
                if (payload_len < 6) {
                    send_ack(g_tcp_client, 1);
                    break;
                }

                uint16_t device_id = 0;
                uint16_t strip_length = 0;
                uint16_t frame_port = 0;
                memcpy(&device_id, payload, 2);
                memcpy(&strip_length, payload + 2, 2);
                memcpy(&frame_port, payload + 4, 2);

                if (strip_length < 1 || strip_length > MAX_DEVICE_PIXELS || frame_port < 1) {
                    send_ack(g_tcp_client, 1);
                    break;
                }

                g_device.reset(new ESPDevice(strip_length));
                g_transport.device_id = device_id;
                g_transport.frame_port = frame_port;
                g_transport.controller_ip = g_tcp_client.remoteIP();
                g_configured = true;
                g_last_sync_seq = 0;
                g_configure_count += 1;

                log_line(
                    "[tcp] configure ok device_id=%u strip_length=%u frame_port=%u controller=%s",
                    device_id,
                    strip_length,
                    frame_port,
                    g_transport.controller_ip.toString().c_str()
                );
                send_ack(g_tcp_client, 0);
                break;
            }

            case CMD_SYNC_RESULT: {
                if (!g_configured || !g_device || payload_len < 14) {
                    break;
                }
                uint16_t seq = 0;
                uint32_t boot_token = 0;
                int64_t offset_us = 0;
                memcpy(&seq, payload, 2);
                memcpy(&boot_token, payload + 2, 4);
                memcpy(&offset_us, payload + 6, 8);
                if (boot_token != g_boot_token) {
                    log_line(
                        "[sync] stale result ignored seq=%u token=%lu current=%lu",
                        static_cast<unsigned>(seq),
                        static_cast<unsigned long>(boot_token),
                        static_cast<unsigned long>(g_boot_token)
                    );
                    break;
                }
                if (seq < g_last_sync_seq) {
                    log_line(
                        "[sync] old result ignored seq=%u last=%u",
                        static_cast<unsigned>(seq),
                        static_cast<unsigned>(g_last_sync_seq)
                    );
                    break;
                }
                g_last_sync_seq = seq;
                g_device->handle_sync_result(offset_us);
                log_line(
                    "[sync] result applied seq=%u offset_us=%lld",
                    static_cast<unsigned>(seq),
                    static_cast<long long>(offset_us)
                );
                log_line("[sync] ready for playback");
                break;
            }

            case CMD_LOAD: {
                if (!g_configured || !g_device) {
                    send_ack(g_tcp_client, 2);
                    break;
                }
                if (payload_len < 4) {
                    send_ack(g_tcp_client, 1);
                    break;
                }

                uint16_t device_id = 0;
                uint16_t gen = 0;
                memcpy(&device_id, payload, 2);
                memcpy(&gen, payload + 2, 2);
                g_transport.device_id = device_id;

                const uint8_t* blob = payload + 4;
                const size_t blob_len = payload_len - 4;
                const bool ok = g_device->handle_load(blob, blob_len, gen);
                g_load_count += 1;
                log_line("[tcp] load gen=%u bytes=%lu status=%s", gen, static_cast<unsigned long>(blob_len), ok ? "ok" : "decode-failed");
                send_ack(g_tcp_client, ok ? 0 : 1);
                break;
            }

            case CMD_START: {
                if (g_configured && g_device && payload_len >= 8) {
                    int64_t t0 = 0;
                    memcpy(&t0, payload, 8);
                    g_device->handle_start(t0);
                    g_start_count += 1;
                    log_line("[tcp] start clock=%s", g_device->playback_uses_sync() ? "synced" : "local");
                }
                break;
            }

            case CMD_JUMP: {
                if (g_configured && g_device && payload_len >= 14) {
                    int64_t t0 = 0;
                    float t_rel = 0.0f;
                    uint16_t gen = 0;
                    memcpy(&t0, payload, 8);
                    memcpy(&t_rel, payload + 8, 4);
                    memcpy(&gen, payload + 12, 2);
                    g_device->handle_jump(t0, t_rel, gen);
                    g_jump_count += 1;
                    log_line(
                        "[tcp] jump t_rel=%.3f gen=%u clock=%s",
                        t_rel,
                        gen,
                        g_device->playback_uses_sync() ? "synced" : "local"
                    );
                }
                break;
            }

            case CMD_PAUSE:
                if (g_configured && g_device) {
                    g_device->handle_pause();
                    g_pause_count += 1;
                    log_line("[tcp] pause");
                }
                break;

            case CMD_RESUME: {
                if (g_configured && g_device && payload_len >= 8) {
                    int64_t t0 = 0;
                    memcpy(&t0, payload, 8);
                    g_device->handle_resume(t0);
                    g_resume_count += 1;
                    log_line("[tcp] resume clock=%s", g_device->playback_uses_sync() ? "synced" : "local");
                }
                break;
            }

            case CMD_STOP:
                if (g_configured && g_device) {
                    g_device->handle_stop();
                    g_stop_count += 1;
                    log_line("[tcp] stop");
                }
                break;

            case CMD_REBOOT:
                log_line("[tcp] reboot requested");
                send_ack(g_tcp_client, 0);
                g_reboot_pending = true;
                g_reboot_deadline_ms = millis() + REBOOT_DELAY_MS;
                log_line("[sys] reboot scheduled");
                break;

            case CMD_DEBUG_SEEK:
            case CMD_DEBUG_STEP:
                log_line("[tcp] ignored debug-only command 0x%02x", cmd_type);
                break;

            default:
                log_line("[tcp] ignored unknown command 0x%02x", cmd_type);
                break;
        }

        g_tcp_buf_used -= total;
        if (g_tcp_buf_used > 0) {
            memmove(g_tcp_buf.data(), g_tcp_buf.data() + total, g_tcp_buf_used);
        }
    }

    return 0;
}

void send_frames()
{
    if (!g_configured || !g_device || g_transport.frame_port == 0) {
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
            g_frames_sent += 1;
            g_have_frame_stats = true;
            g_last_frame_gen = frame.gen;
            g_last_frame_index = frame.frame_index;
            g_last_frame_t_rel = frame.t_rel;
        }
    }
}

void maybe_log_status()
{
    const uint32_t now = millis();
    if (g_last_status_ms != 0 && now - g_last_status_ms < STATUS_INTERVAL_MS) {
        return;
    }
    g_last_status_ms = now;

    const String ip = g_wifi_ready ? WiFi.localIP().toString() : String("-");
    const unsigned long uptime_s = now / 1000;
    if (g_have_frame_stats) {
        log_line(
            "[status] up=%lus wifi=%s ip=%s ctrl=%s cfg=%s tries=%lu ok=%lu hellos=%lu tcp=%lu/%lu frames=%llu last=%u/%lu/%.3f load=%lu start=%lu stop=%lu",
            uptime_s,
            g_wifi_ready ? "up" : "down",
            ip.c_str(),
            yes_no(g_controller_connected),
            yes_no(g_configured),
            static_cast<unsigned long>(g_wifi_connect_attempts),
            static_cast<unsigned long>(g_wifi_connect_successes),
            static_cast<unsigned long>(g_hello_count),
            static_cast<unsigned long>(g_tcp_accept_count),
            static_cast<unsigned long>(g_tcp_disconnect_count),
            static_cast<unsigned long long>(g_frames_sent),
            static_cast<unsigned>(g_last_frame_gen),
            static_cast<unsigned long>(g_last_frame_index),
            static_cast<double>(g_last_frame_t_rel),
            static_cast<unsigned long>(g_load_count),
            static_cast<unsigned long>(g_start_count),
            static_cast<unsigned long>(g_stop_count)
        );
        return;
    }

    log_line(
        "[status] up=%lus wifi=%s ip=%s ctrl=%s cfg=%s tries=%lu ok=%lu hellos=%lu tcp=%lu/%lu frames=%llu load=%lu start=%lu stop=%lu",
        uptime_s,
        g_wifi_ready ? "up" : "down",
        ip.c_str(),
        yes_no(g_controller_connected),
        yes_no(g_configured),
        static_cast<unsigned long>(g_wifi_connect_attempts),
        static_cast<unsigned long>(g_wifi_connect_successes),
        static_cast<unsigned long>(g_hello_count),
        static_cast<unsigned long>(g_tcp_accept_count),
        static_cast<unsigned long>(g_tcp_disconnect_count),
        static_cast<unsigned long long>(g_frames_sent),
        static_cast<unsigned long>(g_load_count),
        static_cast<unsigned long>(g_start_count),
        static_cast<unsigned long>(g_stop_count)
    );
}

}  // namespace

void setup()
{
    Serial.begin(115200);
    delay(200);

    esp_device_init_leds();
    g_device_uid = make_device_uid();
    g_boot_token = make_boot_token();

    log_line("[boot] elements esp32 runtime starting");
    log_line("[boot] build=%s %s", __DATE__, __TIME__);
    log_line("[boot] uid=%s", g_device_uid.c_str());
    log_line("[boot] boot_token=%lu", static_cast<unsigned long>(g_boot_token));
    g_preferences_ready = g_preferences.begin(NVS_NAMESPACE, false);
    if (!g_preferences_ready) {
        log_line("[wifi] failed to open preferences namespace=%s", NVS_NAMESPACE);
    }
    load_last_good_ssid();

    connect_to_dev_wifi();
    ensure_network_services_started();
}

void loop()
{
    ensure_wifi_connected();
    ensure_network_services_started();
    accept_controller();

    if (g_tcp_client && g_tcp_client.connected()) {
        if (poll_tcp_commands() < 0) {
            disconnect_controller("socket error");
        }
    } else {
        handle_disconnect();
    }

    if (g_configured && g_device) {
        g_device->tick_once();
        send_frames();
    }

    maybe_send_hello();
    if (g_server_started) {
        poll_discovery_udp();
    }
    maybe_log_status();
    maybe_reboot();
    delay(LOOP_DELAY_MS);
}
