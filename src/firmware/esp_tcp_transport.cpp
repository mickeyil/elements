#include "esp_tcp_transport.h"

#include <cstring>

namespace controller_link {

EspTcpTransport::~EspTcpTransport()
{
    disconnect();
}

bool EspTcpTransport::connect(uint32_t dst_ip, uint16_t dst_port)
{
    if (_connected) return true;

    // dst_ip is network byte order: byte 0 is the first dotted octet,
    // byte 3 the last. Use IPAddress's 4-arg ctor; the uint32_t one
    // has varying byte-order interpretation across cores.
    uint8_t o[4];
    std::memcpy(o, &dst_ip, 4);
    IPAddress ip(o[0], o[1], o[2], o[3]);

    if (_client.connect(ip, dst_port, TIMEOUT_MS) != 1) {
        // Defensive: own the cleanup rather than trust WiFiClient's
        // failure path.
        _client.stop();
        return false;
    }

    _connected = true;
    return true;
}

void EspTcpTransport::disconnect()
{
    _client.stop();
    _connected = false;
}

int EspTcpTransport::read(uint8_t* dst, size_t n)
{
    if (!_connected) return -1;

    // Drain order matters: when the peer closes, connected() can flip
    // false while bytes are still in the rx buffer. Read those first;
    // only then check liveness.
    if (_client.available() > 0) {
        const int r = _client.read(dst, n);
        if (r <= 0) {
            // available() said something was there but read failed;
            // treat as a dead socket.
            disconnect();
            return -1;
        }
        return r;
    }
    if (!_client.connected()) {
        disconnect();
        return -1;
    }
    return 0;
}

bool EspTcpTransport::write(const uint8_t* src, size_t len)
{
    if (!_connected) return false;

    // WiFiClient::write may return fewer than len on a busy tx buffer.
    // Loop until the buffer is fully accepted; a 0 return means the
    // socket is dead (write timed out internally or peer closed).
    size_t offset = 0;
    while (offset < len) {
        const size_t w = _client.write(src + offset, len - offset);
        if (w == 0) {
            disconnect();
            return false;
        }
        offset += w;
    }
    return true;
}

}  // namespace controller_link
