#pragma once

#include <cstdint>

class DiscoveryClient;
class UdpTransport;
struct DeviceIdentity;

// Forwards buffered slog records to the controller's log port over
// UDP, one datagram per record, fire and forget. Sends only while the
// controller is evidently alive (the link is up, or a fresh OFFER
// arrived); otherwise records wait in the slog ring, so the backlog
// from a controller outage is delivered once it returns, up to the
// ring's capacity. Best effort end to end: a datagram sent while
// nobody listens is gone, and the controller reports sequence gaps
// rather than recovering them.
//
// The port is the controller's log port (link_protocol.h). Wire
// format is in log_sender.cpp, mirrored by the controller's wire.py.

class LogSender
{
public:
    LogSender(UdpTransport& udp, const DiscoveryClient& discovery,
              const DeviceIdentity& identity, uint16_t port);

    // Send up to a few records this tick. link_ready is the owner's
    // controller-liveness signal alongside discovery freshness.
    void tick(bool link_ready);

private:
    UdpTransport&          _udp;
    const DiscoveryClient& _discovery;
    const DeviceIdentity&  _identity;
    const uint16_t         _port;
};
