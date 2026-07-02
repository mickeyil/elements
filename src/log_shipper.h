#pragma once

#include <cstdint>

class DiscoveryClient;
class UdpTransport;
struct DeviceIdentity;

// Forwards buffered slog records to the controller's log port over
// UDP, one datagram per record, fire and forget. Ships only while the
// controller is evidently alive (the link is up, or a fresh OFFER
// arrived); otherwise records wait in the slog ring, so the backlog
// from a controller outage is delivered once it returns, up to the
// ring's capacity. Best effort end to end: a datagram sent while
// nobody listens is gone, and the controller reports sequence gaps
// rather than recovering them.
//
// The port is a compile-time constant on the device, like the
// discovery and sync ports; controller.log_port in the config must
// match it. Wire format is in log_shipper.cpp, mirrored by the
// controller's wire.py.

class LogShipper
{
public:
    LogShipper(UdpTransport& udp, const DiscoveryClient& discovery,
               const DeviceIdentity& identity);

    // Ship up to a few records this tick. link_ready is the owner's
    // controller-liveness signal alongside discovery freshness.
    void tick(bool link_ready);

private:
    UdpTransport&          _udp;
    const DiscoveryClient& _discovery;
    const DeviceIdentity&  _identity;
};
