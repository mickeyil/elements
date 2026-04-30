#pragma once

#include <cstddef>
#include <cstdint>

// Abstract TCP transport. Two impls live elsewhere:
//   - EspTcpTransport   (Arduino WiFiClient on firmware)
//   - PosixTcpTransport (BSD sockets on host/sim)
//
// The transport concerns itself ONLY with moving bytes over a single
// outbound TCP connection. The device dials the controller; never the
// other way around. Anything above transport -- discovery, identity,
// command framing, ACK policy, reconnect strategy, telemetry, logging
// -- lives in the caller.
//
// The operating environment is flaky: Wi-Fi can drop, the controller
// can disappear at any time, and the device is expected to recover
// smoothly. Every method is defined for every state, idempotent where
// it makes sense, and surfaces failure synchronously via return values.

namespace controller_link {

class TcpTransport {
public:
    virtual ~TcpTransport() = default;

    // Dial controller_ipv4_be:controller_port. Bounded by the impl's
    // own connect timeout (~500ms on ESP via WiFiClient::connect with
    // an explicit timeout; similar on host). Idempotent: calling while
    // already connected is a no-op that returns true. Returns false on
    // timeout, refused, or unreachable; the transport is left in the
    // not-connected state and the caller may back off and retry.
    virtual bool connect(uint32_t controller_ipv4_be,
                         uint16_t controller_port) = 0;

    // Drop the connection. Idempotent. Always succeeds.
    virtual void disconnect() = 0;

    // True iff the connection is currently usable. Cheap getter --
    // returns transport-owned cached state, never issues a probe.
    // Flips to false only when read() or write() detects the socket
    // is dead, or after disconnect().
    virtual bool is_connected() const = 0;

    // Read up to n bytes from the connected client into dst.
    //   >  0  bytes were read; the returned count is written into dst.
    //   == 0  no data available right now (non-blocking; try again
    //         next tick). Connection still considered alive.
    //   <  0  socket error or peer closed. The transport considers
    //         itself not connected after this; is_connected() is false.
    //         Caller drops in-progress framing and goes back to
    //         discovery + connect.
    //
    // Calling while !is_connected() returns < 0.
    virtual int read(uint8_t* dst, size_t n) = 0;

    // Send all len bytes. All-or-fail within an impl-defined timeout
    // (~500 ms on ESP, similar on host). Returns:
    //   true   all len bytes were accepted by the socket layer within
    //          the timeout;
    //   false  socket error, partial write, peer close, or timeout;
    //          the transport considers itself not connected after this
    //          and is_connected() returns false.
    //
    // Calling while !is_connected() returns false.
    virtual bool write(const uint8_t* src, size_t len) = 0;
};

}  // namespace controller_link
