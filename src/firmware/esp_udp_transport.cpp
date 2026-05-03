#include "esp_udp_transport.h"

#include <cstring>

EspUdpTransport::~EspUdpTransport()
{
    close();
}

bool EspUdpTransport::bind(uint16_t local_port)
{
    if (_bound) {
        // WiFiUDP doesn't expose the kernel-assigned ephemeral port,
        // so _bound_port stays 0 after bind(0). bind(0) while bound
        // is always a no-op; otherwise we can only match the
        // original p.
        if (local_port == _bound_port) return true;
        if (local_port == 0)           return true;
        return false;
    }

    if (_udp.begin(local_port) != 1) {
        // Defensive: own the cleanup rather than trust WiFiUDP's
        // failure path. stop() is safe on uninitialized state.
        _udp.stop();
        return false;
    }

    _bound      = true;
    _bound_port = local_port;
    return true;
}

void EspUdpTransport::close()
{
    // Always stop(); safe before begin() and idempotent after. Don't
    // gate on _bound: avoids drifting from WiFiUDP's own state.
    _udp.stop();
    _bound      = false;
    _bound_port = 0;
}

bool EspUdpTransport::send(const uint8_t* src, size_t len,
                           uint32_t dst_ip, uint16_t dst_port)
{
    if (!_bound) return false;
    // WiFiUDP::write auto-flushes its 1460-byte tx buffer mid-payload,
    // splitting longer writes into multiple packets. Reject before
    // beginPacket() so the contract holds.
    if (len > MAX_PAYLOAD_SIZE) return false;

    // dst_ip is network byte order: byte 0 is the first dotted octet,
    // byte 3 the last. Use IPAddress's 4-arg ctor; the uint32_t one
    // has varying byte-order interpretation across cores.
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
        // 0 = no packet (and an empty packet, which WiFiUDP can't
        // distinguish; folded onto this case per the contract).
        return 0;
    }

    const int r = _udp.read(dst, n);
    if (r <= 0) {
        // parsePacket said something was there but read failed: flush
        // any residue so the next parsePacket() can fetch fresh.
        // WiFiUDP returns 0 from parsePacket while rx_buffer holds
        // leftovers.
        _udp.flush();
        return 0;
    }
    if (r < avail) {
        // Caller's buffer is smaller than the packet. Match POSIX
        // recvfrom: return what fits, discard the tail. Without this,
        // residue blocks every subsequent parsePacket() until rebind.
        _udp.flush();
    }

    const IPAddress ip = _udp.remoteIP();
    uint8_t o[4] = { ip[0], ip[1], ip[2], ip[3] };
    std::memcpy(src_ip, o, 4);
    *src_port = _udp.remotePort();
    return r;
}
