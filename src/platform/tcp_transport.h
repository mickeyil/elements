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
//   write(src, len)    non-blocking send; returns bytes accepted.
//   disconnect()       drop the connection.
//   is_connected()     whether a connection is currently up.
//
// IPv4-only. Addresses are uint32_t in network byte order.
// Single-threaded; implementations do no internal locking.
//
// Implementations:
//   - PosixTcpTransport (host/sim, BSD sockets)
//   - EspTcpTransport   (firmware, Arduino WiFiClient)

class TcpTransport {
public:
    // Maximum time connect() may block before failing. connect() is
    // the one call allowed to block: it runs only against a
    // freshly-offered controller, never in the steady-state tick.
    static constexpr int CONNECT_TIMEOUT_MS = 500;

    virtual ~TcpTransport() = default;

    // Open a connection to dst_ip:dst_port. Blocks up to
    // CONNECT_TIMEOUT_MS. A second call while already connected
    // returns true without re-opening. Returns false on timeout,
    // refused, or unreachable; the transport stays not-connected and
    // the caller may retry.
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

    // Send up to len bytes without blocking (POSIX write(2)
    // semantics on a non-blocking socket). Returns:
    //   >  0  bytes the stack accepted (may be < len).
    //   == 0  no buffer space right now (try again next tick).
    //   <  0  socket error or peer closed; is_connected() flips
    //         false. The caller should drop and reconnect.
    virtual int write(const uint8_t* src, size_t len) = 0;
};
