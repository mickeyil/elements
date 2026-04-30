#pragma once

#include <cstddef>
#include <cstdint>

// Abstract UDP transport. Two impls live elsewhere:
//   - PosixUdpTransport (BSD sockets on host/sim; src/posix_udp_transport.{h,cpp})
//   - EspUdpTransport   (Arduino WiFiUDP on firmware; src/firmware/esp_udp_transport.{h,cpp})
//
// IPv4-only. Addresses are uint32_t in network byte order; the same byte
// layout the OS already uses (sin_addr.s_addr, IPAddress's 4-byte view).
//
// Single-threaded. The App ticks transport consumers from one loop;
// callers do not synchronize across threads. Impls do no internal
// locking.
//
// UDP is connectionless: bind a local port (or 0 for ephemeral), then
// send/recv with explicit destination/source addresses. There is no
// connect/disconnect symmetry with TcpTransport.
//
// Two callers use this interface; both bind ephemeral local ports:
//   - the discovery side of ControllerLink (binds ephemeral; broadcasts
//     HELLO to the controller's well-known discovery port; recvs
//     OFFER/REJECT unicast back at that ephemeral port). The well-known
//     discovery port belongs to the controller, which binds it to
//     listen for HELLOs; the device never binds it. This also lets
//     multiple sim devices coexist on one host without fighting over a
//     shared port.
//   - ClockSyncClient (binds an ephemeral local port; sends pings to
//     the controller's sync port; recvs pongs).
//
// Broadcast: impls enable broadcast on every bound socket (SO_BROADCAST
// on Posix; no-op on Arduino, which already permits broadcast sends).
// There is no broadcast knob on the interface.
//
// Connection state: send/recv failures do NOT auto-release the socket;
// UDP has no connection state to lose. close() is the only call that
// releases the bound port. Callers that want to recover from a send
// error by re-binding must call close() then bind() themselves.
//
// Both callers treat send() as fire-and-forget (UDP loss is normal and
// the upper layer retries on its own schedule) and recv() as
// non-blocking drain.

namespace controller_link {

class UdpTransport {
public:
    virtual ~UdpTransport() = default;

    // Bind a local port. Pass 0 for an OS-assigned ephemeral port.
    // Returns false if the bind fails (port in use, no permission,
    // network down).
    //
    // Rebind semantics, in terms of the current bound port (which after
    // bind(0) is the actual OS-assigned port, not 0):
    //   - bind(p) where p == current bound port: no-op, returns true.
    //   - bind(p) where p != current bound port and p != 0: returns
    //     false; the caller must close() first.
    //   - bind(0) while already bound: no-op against the current port,
    //     returns true. Treating this as "give me a new port" would
    //     force impls to silently rotate sockets, which no caller wants.
    virtual bool bind(uint16_t local_port) = 0;

    // Release the local port. Idempotent. Always succeeds.
    virtual void close() = 0;

    // True iff a local port is currently bound. Does not promise
    // the next send/recv will succeed; only that a socket is open.
    virtual bool is_bound() const = 0;

    // Send len bytes to dst_ip:dst_port. dst_ip is in network byte
    // order. Returns false on socket error or if not bound; the
    // caller does not retry (the upper layer's own schedule handles
    // loss). A failed send does NOT release the socket; the bound
    // state is unchanged. Callers that want to recover by re-binding
    // must call close() then bind() explicitly.
    virtual bool send(const uint8_t* src, size_t len,
                      uint32_t dst_ip, uint16_t dst_port) = 0;

    // Read up to n bytes from the bound socket into dst. On a returned
    // count > 0, the source address is written into *src_ip and
    // *src_port (both pointers must be non-null). Returns:
    //   >  0  packet received; src_ip/src_port populated.
    //   == 0  no useful datagram available right now (try again next
    //         tick). A genuinely-empty datagram is consumed and folded
    //         onto this case; the protocol never produces zero-byte
    //         packets, and ESP's WiFiUDP cannot distinguish an empty
    //         datagram from no datagram in any event.
    //   <  0  socket error or not bound. Like send(), this does NOT
    //         release the socket; close() is the only release call.
    virtual int recv(uint8_t* dst, size_t n,
                     uint32_t* src_ip, uint16_t* src_port) = 0;
};

}  // namespace controller_link
