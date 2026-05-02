#pragma once

#include <WiFi.h>
#include <WiFiClient.h>

#include <cstddef>
#include <cstdint>

#include "../tcp_transport.h"

// Arduino-WiFiClient implementation of TcpTransport. Selected for the
// ARDUINO build via platformio.ini's build_src_filter.
//
// WiFiClient::connect already takes a millisecond timeout; available()
// + read() makes read() effectively non-blocking; write() loops until
// every byte is sent or the platform's internal write timeout fires.
//
// Owns a WiFiClient; copy and move are deleted.

namespace controller_link {

class EspTcpTransport : public TcpTransport {
public:
    EspTcpTransport() = default;
    ~EspTcpTransport() override;

    EspTcpTransport(const EspTcpTransport&) = delete;
    EspTcpTransport& operator=(const EspTcpTransport&) = delete;
    EspTcpTransport(EspTcpTransport&&) = delete;
    EspTcpTransport& operator=(EspTcpTransport&&) = delete;

    bool connect(uint32_t controller_ipv4_be,
                 uint16_t controller_port) override;
    void disconnect() override;
    bool is_connected() const override { return _connected; }
    int  read(uint8_t* dst, size_t n) override;
    bool write(const uint8_t* src, size_t len) override;

private:
    WiFiClient _client;
    bool       _connected = false;
};

}  // namespace controller_link
