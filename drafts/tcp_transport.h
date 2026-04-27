#pragma once

#include <cstddef>
#include <cstdint>

// Abstract TCP transport. Two impls live elsewhere:
//   - EspTcpTransport  (WiFiServer / WiFiClient, on firmware)
//   - PosixTcpTransport (BSD sockets, on host/sim)
//
// The transport owns socket lifecycle (server bind, accept, drop). The
// parser drives reads and writes.

namespace controller_link {

// Connected peer endpoint (IPv4 only -- enough for this project today).
// SessionHandler captures this on Attach so the outbound UDP frame loop
// has a destination.
struct PeerAddress {
    uint32_t ipv4_be = 0;  // big-endian (network byte order)
    uint16_t port    = 0;
};

class TcpTransport {
public:
    virtual ~TcpTransport() = default;

    // True iff a controller is currently connected.
    virtual bool is_connected() const = 0;

    // Read up to n bytes into dst. Returns bytes read; 0 means no data
    // available right now (non-blocking semantics). Negative means socket
    // error -- caller drops the client.
    virtual int read(uint8_t* dst, size_t n) = 0;

    // Send all len bytes. Returns false on partial write or socket error.
    // Today firmware blocks until the kernel accepts everything; the
    // interface preserves that.
    virtual bool write(const uint8_t* src, size_t len) = 0;

    // Close the active client (server stays listening). reason is for
    // logging only.
    virtual void close_client(const char* reason = nullptr) = 0;

    // Connected client's IPv4 + source port. Defined behavior only while
    // is_connected() is true; returns a zeroed PeerAddress otherwise.
    virtual PeerAddress peer_address() const = 0;

    // TODO: connection lifecycle events (accept, drop) are pulled by the
    // parser via is_connected() per tick. If a richer event stream is
    // needed (e.g., for telemetry), add a callback or a poll-time event
    // struct here. Today polling is enough.
};

}  // namespace controller_link
