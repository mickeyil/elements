#pragma once

#include <cstdint>

class UdpTransport;
struct DeviceIdentity;

// IPv4 broadcast (network byte order).
constexpr uint32_t IPV4_BROADCAST = 0xFFFFFFFFu;

// DiscoveryClient finds the controller on the LAN over UDP. The
// owner polls it while no TCP link is up; once an OFFER arrives,
// controller_ip() and tcp_port() expose where to connect.
//
// Wire (multi-byte fields little-endian):
//   DISCOVER  device  -> broadcast  { magic, type=0x01, uid (16B) }
//   OFFER     control -> device     { magic, type=0x02, ipv4 (4B), port (2B) }
//
// Most-recent-OFFER-wins; stale values are not cleared. A TCP
// connect against a stale ip/port fails, the owner retries, and the
// next OFFER updates the values. Same poll / lazy-bind pattern as
// ClockSyncClient.

class DiscoveryClient {
public:
    DiscoveryClient(UdpTransport& udp, const DeviceIdentity& identity,
                    uint32_t dst_ip = IPV4_BROADCAST);

    // Per-tick entry: bind if needed, drain replies, broadcast on schedule.
    void poll();

    // Most recent controller IPv4 (network byte order); 0 until the
    // first OFFER arrives.
    uint32_t controller_ip() const;

    // Most recent controller TCP port; 0 until the first OFFER arrives.
    uint16_t tcp_port() const;

private:
    void drain_responses_();
    void send_discover_();
    int64_t now_us_() const;

    UdpTransport&         _udp;
    const DeviceIdentity& _identity;
    const uint32_t        _dst_ip;

    // Latest OFFER fields; 0 = not yet received.
    uint32_t _controller_ip = 0;
    uint16_t _tcp_port      = 0;

    // Last DISCOVER send time; 0 fires immediately on next poll().
    int64_t _last_broadcast_us = 0;
};
