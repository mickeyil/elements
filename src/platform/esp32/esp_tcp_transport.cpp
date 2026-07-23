#include "platform/esp32/esp_tcp_transport.h"

#include <sys/socket.h>

#include <cerrno>
#include <cstring>

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

    if (_client.connect(ip, dst_port, CONNECT_TIMEOUT_MS) != 1) {
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
    if (n == 0)      return 0;

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

int EspTcpTransport::write(const uint8_t* src, size_t len)
{
    if (!_connected) return -1;
    if (len == 0) return 0;

    // Bypass WiFiClient::write: its retry budget resets on partial
    // progress and can block for tens of seconds. One non-blocking
    // send on the lwIP fd; the caller keeps the unsent tail.
    const int fd = _client.fd();
    if (fd < 0) {
        disconnect();
        return -1;
    }

    const ssize_t w = ::send(fd, src, len, MSG_DONTWAIT);
    if (w > 0) return static_cast<int>(w);
    if (w == 0) return 0;
    if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) return 0;
    disconnect();
    return -1;
}
