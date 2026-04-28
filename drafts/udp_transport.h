#pragma once

#include <cstddef>
#include <cstdint>

// Abstract UDP transport. Two impls live elsewhere:
//   - EspUdpTransport   (wraps Arduino WiFiUDP on firmware)
//   - PosixUdpTransport (BSD sockets on host/sim)
//
// UDP is connectionless: bind a local port (or 0 for ephemeral), then
// send/recv with explicit destination/source addresses. There is no
// connect/disconnect symmetry with TcpTransport.
//
// Two callers use this interface:
//   - the discovery side of ControllerLink (binds the well-known
//     discovery port; broadcasts HELLO; recvs OFFER/REJECT).
//   - ClockSyncClient (binds an ephemeral local port; sends pings to
//     the controller's sync port; recvs pongs).
//
// Both treat send() as fire-and-forget (UDP loss is normal and the
// upper layer retries on cadence) and recv() as non-blocking drain.

namespace controller_link {

class UdpTransport {
public:
    virtual ~UdpTransport() = default;

    // Bind a local port. Pass 0 for an OS-assigned ephemeral port.
    // Returns false if the bind fails (port in use, no permission,
    // network down). Idempotent: a successful re-bind on the same
    // local port is a no-op returning true.
    virtual bool bind(uint16_t local_port) = 0;

    // Release the local port. Idempotent. Always succeeds.
    virtual void close() = 0;

    // Send len bytes to dst_ip:dst_port. dst_ip is in network byte
    // order. Returns false on socket error or if not bound; the caller
    // does not retry (the upper layer's cadence handles loss).
    virtual bool send(const uint8_t* src, size_t len,
                      uint32_t dst_ip, uint16_t dst_port) = 0;

    // Read up to n bytes from the bound socket into dst. On a returned
    // count > 0, the source address is written into *src_ip and
    // *src_port (both pointers must be non-null). Returns:
    //   >  0  packet received; src_ip/src_port populated.
    //   == 0  no datagram available right now (try again next tick).
    //   <  0  socket error or not bound.
    virtual int recv(uint8_t* dst, size_t n,
                     uint32_t* src_ip, uint16_t* src_port) = 0;
};

}  // namespace controller_link
