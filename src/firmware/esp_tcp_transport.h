#pragma once

#include <WiFi.h>
#include <WiFiClient.h>

#include <cstddef>
#include <cstdint>

#include "../tcp_transport.h"

// Arduino-WiFiClient implementation of TcpTransport. Selected for the
// ARDUINO build via platformio.ini's build_src_filter.
//
// connect() passes TIMEOUT_MS to WiFiClient::connect. read() is
// non-blocking via available() + read(). write() loops until every
// byte is sent; the actual write bound is WiFiClient's own internal
// timeout, not TIMEOUT_MS.
//
// Owns a WiFiClient; copy and move are deleted.

class EspTcpTransport : public TcpTransport {
public:
    ~EspTcpTransport() override;

    bool connect(uint32_t dst_ip, uint16_t dst_port) override;
    void disconnect() override;
    bool is_connected() const override { return _connected; }
    int  read(uint8_t* dst, size_t n) override;
    bool write(const uint8_t* src, size_t len) override;

private:
    WiFiClient _client;
    bool       _connected = false;
};
