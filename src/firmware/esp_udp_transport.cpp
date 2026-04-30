#include "esp_udp_transport.h"

#include <cstring>

namespace controller_link {

EspUdpTransport::~EspUdpTransport()
{
    close();
}

bool EspUdpTransport::bind(uint16_t local_port)
{
    if (_bound) {
        // Same rebind rules as PosixUdpTransport. WiFiUDP doesn't
        // expose the kernel-assigned ephemeral port, so for bind(0)
        // we record 0 and only the bind(0)-while-bound case lets the
        // caller no-op a follow-up. That's all the consumers need.
        if (local_port == _bound_port) return true;
        if (local_port == 0)           return true;
        return false;
    }

    if (_udp.begin(local_port) != 1) {
        return false;
    }

    _bound      = true;
    _bound_port = local_port;
    return true;
}

void EspUdpTransport::close()
{
    if (_bound) {
        _udp.stop();
        _bound      = false;
        _bound_port = 0;
    }
}

bool EspUdpTransport::send(const uint8_t* src, size_t len,
                           uint32_t dst_ip, uint16_t dst_port)
{
    if (!_bound) return false;

    // dst_ip is in network byte order: byte 0 is the first dotted
    // octet, byte 3 is the last. Construct IPAddress through its
    // public 4-arg ctor instead of relying on the uint32_t ctor's
    // (varying) byte-order interpretation.
    uint8_t o[4];
    std::memcpy(o, &dst_ip, 4);
    IPAddress ip(o[0], o[1], o[2], o[3]);

    if (_udp.beginPacket(ip, dst_port) != 1) return false;
    if (_udp.write(src, len) != len)         return false;
    return _udp.endPacket() == 1;
}

int EspUdpTransport::recv(uint8_t* dst, size_t n,
                          uint32_t* src_ip, uint16_t* src_port)
{
    if (!_bound) return -1;

    const int avail = _udp.parsePacket();
    if (avail <= 0) {
        // 0 = no packet; on ESP an empty datagram is also reported as
        // 0 length and folded onto this case per the contract.
        return 0;
    }

    const int r = _udp.read(dst, n);
    if (r <= 0) {
        // parsePacket said something was there but read failed: treat
        // as a transport hiccup, not a hard error. Caller retries.
        return 0;
    }

    const IPAddress ip = _udp.remoteIP();
    uint8_t o[4] = { ip[0], ip[1], ip[2], ip[3] };
    std::memcpy(src_ip, o, 4);
    *src_port = _udp.remotePort();
    return r;
}

}  // namespace controller_link
