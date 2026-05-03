#include "esp_tcp_transport.h"

#include <sys/select.h>
#include <sys/socket.h>
#include <sys/time.h>

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

    // Bypass WiFiClient::write: its retry budget resets on partial
    // progress and can block for tens of seconds. Drive the lwIP fd
    // directly with our own deadline.
    const int fd = _client.fd();
    if (fd < 0) {
        disconnect();
        return false;
    }

    const uint32_t start = millis();
    size_t written = 0;
    while (written < len) {
        const ssize_t w = ::send(fd, src + written, len - written,
                                 MSG_DONTWAIT);
        if (w > 0) {
            written += static_cast<size_t>(w);
            continue;
        }
        if (w < 0 && errno == EINTR) continue;
        if (w < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) {
            // tx buffer full; wait for writability up to the deadline.
            const uint32_t elapsed = millis() - start;
            if (elapsed >= static_cast<uint32_t>(TIMEOUT_MS)) {
                disconnect();
                return false;
            }
            const uint32_t left_ms = TIMEOUT_MS - elapsed;
            timeval tv{};
            tv.tv_sec  = left_ms / 1000;
            tv.tv_usec = (left_ms % 1000) * 1000;
            fd_set wfds;
            FD_ZERO(&wfds);
            FD_SET(fd, &wfds);
            const int s = ::select(fd + 1, nullptr, &wfds, nullptr, &tv);
            if (s < 0 && errno == EINTR) continue;
            if (s <= 0) {
                disconnect();
                return false;
            }
            continue;
        }
        // Hard error.
        disconnect();
        return false;
    }
    return true;
}
