#pragma once

#include <cstddef>
#include <cstdint>

// A thin wrapper around the platform's UDP socket. Lets the app send
// and receive UDP packets without caring whether it runs on Arduino
// (WiFiUDP) or POSIX (BSD sockets).
//
// Usage:
//   bind(port)               claim a local port (0 = OS-assigned).
//   send(buf, len, ip, port) send one UDP packet to a peer.
//   recv(buf, n, &ip, &port) read one packet, learn who sent it.
//   close()                  release the local port.
//
// IPv4-only. Addresses are uint32_t in network byte order.
// Single-threaded; implementations do no internal locking.
//
// Implementations:
//   - PosixUdpTransport (host/sim, BSD sockets)
//   - EspUdpTransport   (firmware, Arduino WiFiUDP)

namespace controller_link {

// Largest payload that fits in one UDP packet. Anything bigger gets
// silently split into multiple packets by the underlying implementation.
constexpr size_t MAX_PAYLOAD_SIZE = 1460;

class UdpTransport {
public:
    virtual ~UdpTransport() = default;

    // Claim a local port. Pass 0 for an OS-assigned ephemeral port
    // (client mode; the usual choice). Specific port numbers belong to
    // server mode, where peers send to a known address. Returns false if
    // the bind fails (port in use, no permission, network down).
    //
    // Re-binding while already bound: same port is a no-op success;
    // different port fails (call close() first); bind(0) is a no-op
    // success against the current port; it never rotates.
    virtual bool bind(uint16_t local_port) = 0;

    // Release the local port.
    virtual void close() = 0;

    // Is a local port currently bound? bind()/close() are the only things
    // that change this.
    virtual bool is_bound() const = 0;

    // Send len bytes to dst_ip:dst_port as a single UDP packet. Returns
    // false if not bound, on socket error, or if len > MAX_PAYLOAD_SIZE.
    virtual bool send(const uint8_t* src, size_t len,
                      uint32_t dst_ip, uint16_t dst_port) = 0;

    // Read up to n bytes from the bound socket into dst. Returns:
    //   >  0  bytes received (1..n); src_ip/src_port populated.
    //   == 0  nothing useful: no packet waiting, or an empty packet
    //         (this protocol never sends empty packets).
    //   <  0  socket error or not bound.
    virtual int recv(uint8_t* dst, size_t n,
                     uint32_t* src_ip, uint16_t* src_port) = 0;
};

}  // namespace controller_link
