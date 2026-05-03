#pragma once

#include <cstddef>
#include <cstdint>

#include "udp_transport.h"

// BSD-sockets implementation of UdpTransport. Compiles on Linux and
// macOS off the same source.
//
// Owns one non-blocking AF_INET/SOCK_DGRAM socket between bind() and
// close(). SO_BROADCAST is enabled on every bound socket so the
// discovery side of ControllerLink can send HELLOs to 255.255.255.255
// without per-call setup.
//
// Owns an fd; copy and move are deleted.

namespace controller_link {

class PosixUdpTransport : public UdpTransport {
public:
    ~PosixUdpTransport() override;

    bool bind(uint16_t local_port) override;
    void close() override;
    bool is_bound() const override { return _fd >= 0; }
    bool send(const uint8_t* src, size_t len,
              uint32_t dst_ip, uint16_t dst_port) override;
    int  recv(uint8_t* dst, size_t n,
              uint32_t* src_ip, uint16_t* src_port) override;

    // Test-only. The kernel-assigned port (the ephemeral after
    // bind(0); p after bind(p)). Returns 0 when unbound.
    uint16_t local_port() const { return _bound_port; }

private:
    int      _fd = -1;
    uint16_t _bound_port = 0;
};

}  // namespace controller_link
