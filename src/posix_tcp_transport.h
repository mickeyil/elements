#pragma once

#include <cstddef>
#include <cstdint>

#include "tcp_transport.h"

// BSD-sockets implementation of TcpTransport. Compiles on Linux and
// macOS off the same source.
//
// Owns one non-blocking AF_INET/SOCK_STREAM socket between connect()
// and disconnect(). Connect uses select() with a bounded timeout;
// read() returns immediately; write() loops until len bytes are
// accepted or the write timeout expires.
//
// SIGPIPE on a peer-close mid-write is suppressed: SO_NOSIGPIPE on
// macOS, MSG_NOSIGNAL on Linux. The caller never has to install a
// signal handler.
//
// Owns an fd; copy and move are deleted.

namespace controller_link {

class PosixTcpTransport : public TcpTransport {
public:
    PosixTcpTransport() = default;
    ~PosixTcpTransport() override;

    PosixTcpTransport(const PosixTcpTransport&) = delete;
    PosixTcpTransport& operator=(const PosixTcpTransport&) = delete;
    PosixTcpTransport(PosixTcpTransport&&) = delete;
    PosixTcpTransport& operator=(PosixTcpTransport&&) = delete;

    bool connect(uint32_t controller_ipv4_be,
                 uint16_t controller_port) override;
    void disconnect() override;
    bool is_connected() const override { return _fd >= 0; }
    int  read(uint8_t* dst, size_t n) override;
    bool write(const uint8_t* src, size_t len) override;

private:
    int _fd = -1;
};

}  // namespace controller_link
