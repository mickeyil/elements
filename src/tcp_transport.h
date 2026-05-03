#pragma once

#include <cstddef>
#include <cstdint>

// A thin wrapper around the platform's TCP socket. Lets the app
// initiate an outbound connection to a peer and exchange bytes
// without caring whether it runs on Arduino (WiFiClient) or POSIX
// (BSD sockets).
//
// Usage:
//   connect(ip, port)  initiate a connection to a peer.
//   read(dst, n)       non-blocking read; reports peer close as < 0.
//   write(src, len)    all-or-fail send within TIMEOUT_MS.
//   disconnect()       drop the connection.
//   is_connected()     whether a connection is currently up.
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
    // Maximum time connect() and write() may block before failing.
    static constexpr int TIMEOUT_MS = 500;

    virtual ~TcpTransport() = default;

    // Open a connection to dst_ip:dst_port. Blocks up to TIMEOUT_MS.
    // A second call while already connected returns true without
    // re-opening. Returns false on timeout, refused, or unreachable;
    // the transport stays not-connected and the caller may retry.
    virtual bool connect(uint32_t dst_ip, uint16_t dst_port) = 0;

    // Drop the connection.
    virtual void disconnect() = 0;

    // Is the connection currently usable?
    virtual bool is_connected() const = 0;

    // Read up to n bytes from the connected socket into dst. Returns:
    //   >  0  bytes received (1..n).
    //   == 0  no data right now (try again next tick).
    //   <  0  socket error or peer closed; is_connected() flips false.
    //         Caller should drop in-progress framing and reconnect.
    virtual int read(uint8_t* dst, size_t n) = 0;

    // Send all len bytes, all-or-fail within TIMEOUT_MS. Returns
    // false on socket error, partial write, peer close, or timeout;
    // is_connected() flips false on any of those.
    virtual bool write(const uint8_t* src, size_t len) = 0;
};

}  // namespace controller_link
