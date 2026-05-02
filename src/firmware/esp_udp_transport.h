#pragma once

#include <WiFiUdp.h>

#include <cstddef>
#include <cstdint>

#include "../udp_transport.h"

// Arduino-WiFiUDP implementation of UdpTransport. Selected for the
// ARDUINO build via platformio.ini's build_src_filter.
//
// WiFiUDP already non-blocks and already permits broadcast sends, so
// neither needs an explicit setup step here.
//
// Owns a WiFiUDP socket; copy and move are deleted.

namespace controller_link {

class EspUdpTransport : public UdpTransport {
public:
    EspUdpTransport() = default;
    ~EspUdpTransport() override;

    EspUdpTransport(const EspUdpTransport&) = delete;
    EspUdpTransport& operator=(const EspUdpTransport&) = delete;
    EspUdpTransport(EspUdpTransport&&) = delete;
    EspUdpTransport& operator=(EspUdpTransport&&) = delete;

    bool bind(uint16_t local_port) override;
    void close() override;
    bool is_bound() const override { return _bound; }
    bool send(const uint8_t* src, size_t len,
              uint32_t dst_ip, uint16_t dst_port) override;
    int  recv(uint8_t* dst, size_t n,
              uint32_t* src_ip, uint16_t* src_port) override;

private:
    WiFiUDP  _udp;
    bool     _bound = false;
    uint16_t _bound_port = 0;
};

}  // namespace controller_link
