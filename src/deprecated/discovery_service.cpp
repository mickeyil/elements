#include "discovery_service.h"

#include <Arduino.h>
#include <WiFi.h>
#include <esp_timer.h>

#include <cstring>

#include "diagnostics.h"
#include "link_protocol.h"


void DiscoveryService::begin(const DeviceIdentity& identity)
{
    _identity = &identity;
}

void DiscoveryService::start_if_needed()
{
    if (_started) {
        return;
    }
    if (!_udp.begin(kDiscoveryPort)) {
        log_line("[wifi] failed to bind discovery UDP port %u", kDiscoveryPort);
        return;
    }

    _started = true;
    _last_hello_ms = 0;
    if (_identity != nullptr) {
        log_line(
            "[net] uid=%s ip=%s tcp=%u discovery=%u",
            _identity->uid,
            WiFi.localIP().toString().c_str(),
            kTcpPort,
            kDiscoveryPort
        );
    }
}

void DiscoveryService::stop()
{
    if (!_started) {
        return;
    }
    _udp.stop();
    _started = false;
    _last_hello_ms = 0;
}

void DiscoveryService::poll()
{
    if (!_started) {
        return;
    }

    poll_udp_();
    maybe_send_hello_();
}

DiscoverySnapshot DiscoveryService::snapshot() const
{
    const uint32_t now = millis();
    DiscoverySnapshot snapshot;
    snapshot.started = _started;
    snapshot.hello_suppressed =
        _started && static_cast<int32_t>(now - _hello_backoff_until_ms) < 0;
    snapshot.hello_count = _hello_count;
    return snapshot;
}

void DiscoveryService::poll_udp_()
{
    while (true) {
        const int packet_size = _udp.parsePacket();
        if (packet_size <= 0) {
            return;
        }

        uint8_t buf[64];
        const int n = _udp.read(buf, sizeof(buf));
        if (n <= 0) {
            continue;
        }

        const IPAddress sender_ip = _udp.remoteIP();
        const uint16_t sender_port = _udp.remotePort();

        if (buf[0] == kSyncReq && n >= 15) {
            handle_sync_request_(buf, static_cast<size_t>(n), sender_ip, sender_port);
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

        handle_duplicate_reject_();
    }
}

void DiscoveryService::maybe_send_hello_()
{
    if (_identity == nullptr) {
        return;
    }

    const uint32_t now = millis();
    if (static_cast<int32_t>(now - _hello_backoff_until_ms) < 0) {
        return;
    }
    if (_last_hello_ms != 0 && now - _last_hello_ms < kHelloIntervalMs) {
        return;
    }

    const size_t uid_len = strlen(_identity->uid);
    if (uid_len == 0) {
        return;
    }

    uint8_t pkt[5 + 255];
    memcpy(pkt, &kDiscoveryMagic, 2);
    memcpy(pkt + 2, &kTcpPort, 2);
    pkt[4] = static_cast<uint8_t>(uid_len);
    memcpy(pkt + 5, _identity->uid, uid_len);

    const IPAddress broadcast_ip(255, 255, 255, 255);
    if (_udp.beginPacket(broadcast_ip, kDiscoveryPort)) {
        _udp.write(pkt, 5 + uid_len);
        if (_udp.endPacket() != 0) {
            _hello_count += 1;
        }
    }
    _last_hello_ms = now;
}

void DiscoveryService::handle_sync_request_(
    const uint8_t* packet,
    size_t packet_size,
    IPAddress sender_ip,
    uint16_t sender_port
)
{
    if (_identity == nullptr) {
        return;
    }
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
    memcpy(resp + 3, &_identity->boot_token, 4);
    memcpy(resp + 7, &t1_us, 8);
    memcpy(resp + 15, &t2_us, 8);
    const int64_t t3_us = esp_timer_get_time();
    memcpy(resp + 23, &t3_us, 8);

    if (_udp.beginPacket(sender_ip, sender_port)) {
        _udp.write(resp, sizeof(resp));
        _udp.endPacket();
    }
}

void DiscoveryService::handle_duplicate_reject_()
{
    if (_identity == nullptr) {
        return;
    }

    _hello_backoff_until_ms = millis() + kDuplicateHelloBackoffMs;
    log_line("[discovery] duplicate uid backoff uid=%s", _identity->uid);
}
