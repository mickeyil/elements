#include "posix_udp_transport.h"

#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <cerrno>
#include <cstring>

namespace controller_link {

PosixUdpTransport::~PosixUdpTransport()
{
    close();
}

bool PosixUdpTransport::bind(uint16_t local_port)
{
    if (_fd >= 0) {
        // Rebind rules. Match against the kernel-assigned port, not
        // the original argument: bind(0) populates _bound_port with
        // the ephemeral port, so a caller who happened to pass that
        // exact number to a follow-up bind() also gets a clean no-op.
        if (local_port == _bound_port) return true;
        if (local_port == 0)           return true;
        return false;
    }

    int fd = ::socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) return false;

    int one = 1;
    if (::setsockopt(fd, SOL_SOCKET, SO_BROADCAST, &one, sizeof(one)) < 0) {
        ::close(fd);
        return false;
    }

    // Preserve any flags the kernel set on the new fd, then add
    // O_NONBLOCK. F_GETFL on a fresh SOCK_DGRAM normally returns 0,
    // but the canonical pattern is read-modify-write.
    int flags = ::fcntl(fd, F_GETFL, 0);
    if (flags < 0 || ::fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0) {
        ::close(fd);
        return false;
    }

    sockaddr_in addr{};
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_ANY);
    addr.sin_port        = htons(local_port);
    if (::bind(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        ::close(fd);
        return false;
    }

    // Read back the kernel-assigned port. For a non-zero local_port
    // this round-trips; for bind(0) it reveals the ephemeral choice.
    sockaddr_in actual{};
    socklen_t   alen = sizeof(actual);
    if (::getsockname(fd, reinterpret_cast<sockaddr*>(&actual), &alen) < 0) {
        ::close(fd);
        return false;
    }

    _fd         = fd;
    _bound_port = ntohs(actual.sin_port);
    return true;
}

void PosixUdpTransport::close()
{
    if (_fd >= 0) {
        ::close(_fd);
        _fd = -1;
    }
    _bound_port = 0;
}

bool PosixUdpTransport::send(const uint8_t* src, size_t len,
                             uint32_t dst_ip, uint16_t dst_port)
{
    if (_fd < 0) return false;
    if (len > UDP_TRANSPORT_MAX_DATAGRAM_BYTES) return false;

    sockaddr_in dst{};
    dst.sin_family      = AF_INET;
    dst.sin_addr.s_addr = dst_ip;          // already network byte order
    dst.sin_port        = htons(dst_port);

    const ssize_t n = ::sendto(_fd, src, len, 0,
                               reinterpret_cast<const sockaddr*>(&dst),
                               sizeof(dst));
    return n == static_cast<ssize_t>(len);
}

int PosixUdpTransport::recv(uint8_t* dst, size_t n,
                            uint32_t* src_ip, uint16_t* src_port)
{
    if (_fd < 0) return -1;

    sockaddr_in src{};
    socklen_t   slen = sizeof(src);
    const ssize_t r = ::recvfrom(_fd, dst, n, 0,
                                 reinterpret_cast<sockaddr*>(&src), &slen);
    if (r > 0) {
        *src_ip   = src.sin_addr.s_addr;   // network byte order
        *src_port = ntohs(src.sin_port);
        return static_cast<int>(r);
    }
    if (r == 0) {
        // Empty datagram. Consumed; folded onto "no useful datagram"
        // per the UdpTransport contract.
        return 0;
    }
    if (errno == EAGAIN || errno == EWOULDBLOCK) {
        return 0;
    }
    return -1;
}

}  // namespace controller_link
