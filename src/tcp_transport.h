#pragma once

#include <cstddef>
#include <cstdint>

// A thin wrapper around the platform's TCP socket. Lets the device
// dial a controller and exchange bytes without caring whether it runs
// on Arduino (WiFiClient) or POSIX (BSD sockets).
//
// The device always dials; the transport never accepts inbound TCP.
// Framing, ACKs, identity, and reconnect strategy live above the
// transport; this layer just moves bytes.
//
// Usage:
//   connect(ip, port)  dial the controller.
//   read(dst, n)       non-blocking read; reports peer close as < 0.
//   write(src, len)    all-or-fail send within an impl-defined timeout.
//   disconnect()       drop the connection.
//   is_connected()     cheap getter (cached state, no probe).
//
// IPv4-only. Addresses are uint32_t in network byte order.
// Single-threaded; implementations do no internal locking.
//
// Implementations:
//   - PosixTcpTransport (host/sim, BSD sockets)
//   - EspTcpTransport   (firmware, Arduino WiFiClient)

namespace controller_link {

class TcpTransport {
public:
    virtual ~TcpTransport() = default;

    // Dial controller_ipv4_be:controller_port. Blocks up to an
    // impl-defined timeout (~500 ms). Idempotent: a second call while
    // already connected is a no-op success. Returns false on timeout,
    // refused, or unreachable; the transport stays not-connected and
    // the caller may retry.
    virtual bool connect(uint32_t controller_ipv4_be,
                         uint16_t controller_port) = 0;

    // Drop the connection.
    virtual void disconnect() = 0;

    // Is the connection currently usable? Cheap getter: cached state,
    // no probe. Flips to false only when read()/write() detect the
    // socket is dead, or after disconnect().
    virtual bool is_connected() const = 0;

    // Read up to n bytes from the connected socket into dst. Returns:
    //   >  0  bytes received (1..n).
    //   == 0  no data right now (try again next tick).
    //   <  0  socket error or peer closed; is_connected() flips false.
    //         Caller should drop in-progress framing and reconnect.
    virtual int read(uint8_t* dst, size_t n) = 0;

    // Send all len bytes, all-or-fail within an impl-defined timeout
    // (~500 ms). Returns false on socket error, partial write, peer
    // close, or timeout; is_connected() flips false on any of those.
    virtual bool write(const uint8_t* src, size_t len) = 0;
};

}  // namespace controller_link
