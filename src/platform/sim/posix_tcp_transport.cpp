#include "platform/sim/posix_tcp_transport.h"

#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <sys/select.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>

#include <cerrno>
#include <chrono>

#ifndef MSG_NOSIGNAL
#define MSG_NOSIGNAL 0
#endif

PosixTcpTransport::~PosixTcpTransport()
{
    disconnect();
}

bool PosixTcpTransport::connect(uint32_t dst_ip, uint16_t dst_port)
{
    if (_fd >= 0) return true;  // already connected

    int fd = ::socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return false;

#ifdef SO_NOSIGPIPE
    // macOS: prevent SIGPIPE on send() to a closed peer.
    int one = 1;
    ::setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof(one));
#endif

    // Non-blocking from the start. connect() returns EINPROGRESS and
    // we wait on select() with our own timeout.
    int flags = ::fcntl(fd, F_GETFL, 0);
    if (flags < 0 || ::fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0) {
        ::close(fd);
        return false;
    }

    sockaddr_in addr{};
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = dst_ip;          // already network byte order
    addr.sin_port        = htons(dst_port);

    const int rc = ::connect(fd, reinterpret_cast<sockaddr*>(&addr),
                             sizeof(addr));
    if (rc < 0 && errno != EINPROGRESS) {
        ::close(fd);
        return false;
    }

    if (rc < 0) {
        // Wait for writability up to the deadline, retrying on EINTR.
        const auto deadline = std::chrono::steady_clock::now()
                            + std::chrono::milliseconds(CONNECT_TIMEOUT_MS);
        for (;;) {
            const auto now = std::chrono::steady_clock::now();
            if (now >= deadline) {
                ::close(fd);
                return false;
            }
            const auto left =
                std::chrono::duration_cast<std::chrono::microseconds>(
                    deadline - now);
            timeval tv{};
            tv.tv_sec  = left.count() / 1'000'000;
            tv.tv_usec = left.count() % 1'000'000;
            fd_set wfds;
            FD_ZERO(&wfds);
            FD_SET(fd, &wfds);
            const int s = ::select(fd + 1, nullptr, &wfds, nullptr, &tv);
            if (s > 0) break;
            if (s < 0 && errno == EINTR) continue;
            ::close(fd);
            return false;
        }

        // Writability alone doesn't mean success; SO_ERROR has the
        // real result of the asynchronous connect.
        int err = 0;
        socklen_t elen = sizeof(err);
        if (::getsockopt(fd, SOL_SOCKET, SO_ERROR, &err, &elen) < 0
            || err != 0) {
            ::close(fd);
            return false;
        }
    }

    _fd = fd;
    return true;
}

void PosixTcpTransport::disconnect()
{
    if (_fd >= 0) {
        ::close(_fd);
        _fd = -1;
    }
}

int PosixTcpTransport::read(uint8_t* dst, size_t n)
{
    if (_fd < 0) return -1;
    if (n == 0)  return 0;

    const ssize_t r = ::recv(_fd, dst, n, 0);
    if (r > 0) return static_cast<int>(r);
    if (r == 0) {
        // Peer sent FIN. Treat as a hard close; the contract is that
        // < 0 means "reconnect," and EOF on a TCP read means exactly
        // that.
        disconnect();
        return -1;
    }
    if (errno == EAGAIN || errno == EWOULDBLOCK) return 0;
    if (errno == EINTR) return 0;
    disconnect();
    return -1;
}

int PosixTcpTransport::write(const uint8_t* src, size_t len)
{
    if (_fd < 0) return -1;
    if (len == 0) return 0;

    const ssize_t w = ::send(_fd, src, len, MSG_NOSIGNAL);
    if (w > 0) return static_cast<int>(w);
    if (w == 0) return 0;
    if (errno == EAGAIN || errno == EWOULDBLOCK || errno == EINTR) return 0;
    disconnect();
    return -1;
}
