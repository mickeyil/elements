#pragma once

#include <cstddef>
#include <cstdint>

#include "tcp_transport.h"

// BSD-sockets implementation of TcpTransport.
//
// SIGPIPE on peer-close mid-write is suppressed; the caller never
// has to install a signal handler.

class PosixTcpTransport : public TcpTransport {
public:
    ~PosixTcpTransport() override;

    bool connect(uint32_t dst_ip, uint16_t dst_port) override;
    void disconnect() override;
    bool is_connected() const override { return _fd >= 0; }
    int  read(uint8_t* dst, size_t n) override;
    int  write(const uint8_t* src, size_t len) override;

private:
    int _fd = -1;
};
