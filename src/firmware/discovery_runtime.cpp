#include "discovery_runtime.h"

#include <Arduino.h>
#include <esp_timer.h>

#include <cstring>

#include "diagnostics.h"
#include "wire_constants.h"

namespace firmware {
namespace {

void send_discovery_hello(
    WiFiUDP& discovery_udp,
    DiagnosticsState& diag,
    const RuntimeState& runtime
)
{
    const size_t uid_len = runtime.device_uid.length();
    if (uid_len == 0 || uid_len > 255) {
        return;
    }

    uint8_t pkt[5 + 255];
    memcpy(pkt, &kDiscoveryMagic, 2);
    memcpy(pkt + 2, &kTcpPort, 2);
    pkt[4] = static_cast<uint8_t>(uid_len);
    memcpy(pkt + 5, runtime.device_uid.c_str(), uid_len);

    const IPAddress broadcast_ip(255, 255, 255, 255);
    if (discovery_udp.beginPacket(broadcast_ip, kDiscoveryPort)) {
        discovery_udp.write(pkt, 5 + uid_len);
        if (discovery_udp.endPacket() != 0) {
            diag.hello_count += 1;
        }
    }
}

void handle_sync_request(
    WiFiUDP& discovery_udp,
    const uint8_t* packet,
    size_t packet_size,
    IPAddress sender_ip,
    uint16_t sender_port,
    const RuntimeState& runtime
)
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
    memcpy(resp + 3, &runtime.boot_token, 4);
    memcpy(resp + 7, &t1_us, 8);
    memcpy(resp + 15, &t2_us, 8);
    const int64_t t3_us = esp_timer_get_time();
    memcpy(resp + 23, &t3_us, 8);

    if (discovery_udp.beginPacket(sender_ip, sender_port)) {
        discovery_udp.write(resp, sizeof(resp));
        discovery_udp.endPacket();
    }
}

}  // namespace

void discovery_maybe_send_hello(
    WiFiUDP& discovery_udp,
    WifiState& wifi,
    DiagnosticsState& diag,
    RuntimeState& runtime
)
{
    if (!wifi.server_started || !wifi.ready || wifi.duplicate_uid_rejected) {
        return;
    }

    const uint32_t now = millis();
    if (diag.last_hello_ms == 0 || now - diag.last_hello_ms >= kHelloIntervalMs) {
        send_discovery_hello(discovery_udp, diag, runtime);
        diag.last_hello_ms = now;
    }
}

void discovery_poll_udp(
    WiFiUDP& discovery_udp,
    WifiState& wifi,
    DiagnosticsState& diag,
    RuntimeState& runtime
)
{
    (void)diag;

    while (true) {
        const int packet_size = discovery_udp.parsePacket();
        if (packet_size <= 0) {
            return;
        }

        uint8_t buf[64];
        const int n = discovery_udp.read(buf, sizeof(buf));
        if (n <= 0) {
            continue;
        }

        const IPAddress sender_ip = discovery_udp.remoteIP();
        const uint16_t sender_port = discovery_udp.remotePort();

        if (buf[0] == kSyncReq && n >= 15) {
            handle_sync_request(
                discovery_udp,
                buf,
                static_cast<size_t>(n),
                sender_ip,
                sender_port,
                runtime
            );
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

        wifi.duplicate_uid_rejected = true;
        log_line("[discovery] controller rejected duplicate uid=%s", runtime.device_uid.c_str());
        return;
    }
}

}  // namespace firmware
