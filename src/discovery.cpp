#include "discovery.h"

#include <algorithm>
#include <cstring>

#include "device_identity.h"
#include "platform_clock.h"
#include "udp_transport.h"

namespace {

// Broadcast destination for DISCOVER; the controller listens here.
constexpr uint16_t DISCOVERY_PORT = 6040;

// DISCOVER broadcast period.
constexpr int64_t BROADCAST_INTERVAL_US = 1'500'000;  // 1.5 s

// Magic prefix; filters unrelated UDP traffic on DISCOVERY_PORT.
constexpr uint16_t MAGIC = 0xD1CC;

constexpr uint8_t PKT_DISCOVER = 0x01;
constexpr uint8_t PKT_OFFER    = 0x02;

// Wire layouts. magic and port are little-endian; ipv4 is four
// octets in network order (matches UdpTransport's dst_ip layout).
//   DISCOVER : [magic=2B][type=1B][uid=16B]              = 19 bytes
//   OFFER    : [magic=2B][type=1B][ipv4=4B][port=2B]     = 9  bytes
constexpr size_t DISCOVER_WIRE_SIZE = 2 + 1 + UID_SIZE;
constexpr size_t OFFER_WIRE_SIZE    = 2 + 1 + 4 + 2;

// OFFER field offsets (after the magic + type prefix).
constexpr size_t OFFER_OFF_IP   = 3;
constexpr size_t OFFER_OFF_PORT = 7;

}  // namespace

DiscoveryClient::DiscoveryClient(UdpTransport& udp, const DeviceIdentity& identity,
                                 uint32_t dst_ip)
    : _udp(udp), _identity(identity), _dst_ip(dst_ip)
{
    // Bind is lazy: Wi-Fi may not be up at boot.
}

void DiscoveryClient::poll()
{
    // Lazy bind (ephemeral); controller replies to our source port.
    // A failed bind retries next tick.
    if (!_udp.is_bound() && !_udp.bind(0)) {
        return;
    }

    drain_responses_();

    // drain_responses_ may have closed the socket on a recv error.
    if (!_udp.is_bound()) {
        return;
    }

    const int64_t now = now_us();
    if (_last_broadcast_us == 0 ||
        now - _last_broadcast_us >= BROADCAST_INTERVAL_US)
    {
        send_discover_();
    }
}

void DiscoveryClient::send_discover_()
{
    uint8_t pkt[DISCOVER_WIRE_SIZE] = {};
    std::memcpy(pkt, &MAGIC, 2);
    pkt[2] = PKT_DISCOVER;

    // Copy up to UID_SIZE bytes; trailing slots are already zero.
    const size_t uid_len = std::min(std::strlen(_identity.uid),
                                    static_cast<size_t>(UID_SIZE));
    std::memcpy(pkt + 3, _identity.uid, uid_len);

    if (!_udp.send(pkt, sizeof(pkt), _dst_ip, DISCOVERY_PORT)) {
        // Send failed: close so next tick rebinds.
        _udp.close();
        return;
    }

    _last_broadcast_us = now_us();
}

void DiscoveryClient::drain_responses_()
{
    // One byte larger than OFFER_WIRE_SIZE so the size check below
    // can reject oversized datagrams.
    uint8_t  buf[OFFER_WIRE_SIZE + 1];
    uint32_t src_ip   = 0;
    uint16_t src_port = 0;

    while (true) {
        const int n = _udp.recv(buf, sizeof(buf), &src_ip, &src_port);
        if (n == 0) return;
        if (n < 0) {
            _udp.close();
            return;
        }

        if (n != static_cast<int>(OFFER_WIRE_SIZE)) continue;

        uint16_t magic = 0;
        std::memcpy(&magic, buf, 2);
        if (magic != MAGIC)      continue;
        if (buf[2] != PKT_OFFER) continue;

        uint32_t ip   = 0;
        uint16_t port = 0;
        std::memcpy(&ip,   buf + OFFER_OFF_IP,   4);
        std::memcpy(&port, buf + OFFER_OFF_PORT, 2);

        // Most-recent-wins; just overwrite.
        _controller_ip = ip;
        _tcp_port      = port;
    }
}
