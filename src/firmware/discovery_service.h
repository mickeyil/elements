#pragma once

#include <WiFiUdp.h>

#include <cstddef>
#include <cstdint>

#include "device_identity.h"

namespace firmware {

struct DiscoverySnapshot {
    bool started = false;
    bool hello_suppressed = false;
    uint32_t hello_count = 0;
};

class DiscoveryService {
public:
    void begin(const DeviceIdentity& identity);
    void start_if_needed();
    void stop();
    void poll();
    DiscoverySnapshot snapshot() const;

private:
    void poll_udp_();
    void maybe_send_hello_();
    void handle_sync_request_(const uint8_t* packet, size_t packet_size, IPAddress sender_ip, uint16_t sender_port);
    void handle_duplicate_reject_();

    const DeviceIdentity* _identity = nullptr;
    WiFiUDP _udp;
    bool _started = false;
    uint32_t _last_hello_ms = 0;
    uint32_t _hello_backoff_until_ms = 0;
    uint32_t _hello_count = 0;
};

}  // namespace firmware
