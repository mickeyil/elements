#pragma once

#include <WiFi.h>
#include <WiFiClient.h>

#include <cstddef>
#include <cstdint>

#include "../tcp_transport.h"

// Arduino-WiFiClient implementation of TcpTransport. Selected for the
// ARDUINO build via platformio.ini's build_src_filter.
//
// connect() passes CONNECT_TIMEOUT_MS to WiFiClient::connect. read()
// is non-blocking via available() + read(). write() bypasses
// WiFiClient::write and uses the underlying lwIP fd directly so the
// never-blocks contract holds: WiFiClient::write resets its own retry
// budget on partial progress and can block for tens of seconds.

class EspTcpTransport : public TcpTransport {
public:
    ~EspTcpTransport() override;

    bool connect(uint32_t dst_ip, uint16_t dst_port) override;
    void disconnect() override;
    bool is_connected() const override { return _connected; }
    int  read(uint8_t* dst, size_t n) override;
    int  write(const uint8_t* src, size_t len) override;

private:
    WiFiClient _client;
    bool       _connected = false;
};
