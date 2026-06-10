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
// Wire (magic and port little-endian; ipv4 in network order):
//   DISCOVER  device  -> broadcast  { magic, type=0x01, uid (16B) }
//   OFFER     control -> device     { magic, type=0x02, ipv4 (4B), port (2B) }
//
// Most-recent-OFFER-wins; stale values are not cleared. Owners gate
// connect attempts on has_fresh_offer(): a controller that stopped
// answering DISCOVER is likely gone, and a TCP connect to a dead
// address blocks for its full timeout. Same poll / lazy-bind pattern
// as ClockSyncClient.

class DiscoveryClient {
public:
    DiscoveryClient(UdpTransport& udp, const DeviceIdentity& identity,
                    uint32_t dst_ip = IPV4_BROADCAST);

    // Per-tick entry: bind if needed, drain replies, broadcast on schedule.
    void poll();

    // Most recent controller IPv4 (network byte order); 0 until the
    // first OFFER arrives.
    uint32_t controller_ip() const { return _controller_ip; }

    // Most recent controller TCP port; 0 until the first OFFER arrives.
    uint16_t tcp_port() const { return _tcp_port; }

    // Has an OFFER arrived within the last two broadcast intervals?
    // False means the controller stopped answering DISCOVER.
    bool has_fresh_offer() const;

private:
    void drain_responses_();
    void send_discover_();

    UdpTransport&         _udp;
    const DeviceIdentity& _identity;
    const uint32_t        _dst_ip;

    // Latest OFFER fields; 0 = not yet received.
    uint32_t _controller_ip = 0;
    uint16_t _tcp_port      = 0;
    int64_t  _last_offer_us = 0;

    // Last DISCOVER send time; 0 fires immediately on next poll().
    int64_t _last_broadcast_us = 0;
};
