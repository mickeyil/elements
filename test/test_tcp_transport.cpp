#include <catch2/catch_test_macros.hpp>

#include <arpa/inet.h>
#include <netinet/in.h>
#include <sys/socket.h>
#include <unistd.h>

#include <atomic>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <thread>

#include "platform/sim/posix_tcp_transport.h"

namespace {

constexpr uint32_t LOOPBACK_BE = 0x0100007F;  // 127.0.0.1, network byte order

// One-shot TCP echo server bound to ephemeral on loopback. Constructs
// listening; start() spawns a thread that accepts a single connection
// and echoes everything back until the client closes. stop() (and the
// destructor) tears it down.
class TcpEchoServer {
public:
    TcpEchoServer() {
        _listen_fd = ::socket(AF_INET, SOCK_STREAM, 0);
        REQUIRE(_listen_fd >= 0);
        int one = 1;
        ::setsockopt(_listen_fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));

        sockaddr_in addr{};
        addr.sin_family      = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        addr.sin_port        = htons(0);
        REQUIRE(::bind(_listen_fd,
                       reinterpret_cast<sockaddr*>(&addr),
                       sizeof(addr)) == 0);
        REQUIRE(::listen(_listen_fd, 1) == 0);

        sockaddr_in actual{};
        socklen_t alen = sizeof(actual);
        REQUIRE(::getsockname(_listen_fd,
                              reinterpret_cast<sockaddr*>(&actual),
                              &alen) == 0);
        _port = ntohs(actual.sin_port);
    }

    ~TcpEchoServer() { stop(); }

    uint16_t port() const { return _port; }

    void start() {
        _thread = std::thread([this] {
            int client_fd = ::accept(_listen_fd, nullptr, nullptr);
            if (client_fd < 0) return;
            {
                std::lock_guard<std::mutex> lk(_mu);
                _client_fd = client_fd;
            }
            uint8_t buf[256];
            while (!_stop.load()) {
                const ssize_t r = ::recv(client_fd, buf, sizeof(buf), 0);
                if (r <= 0) break;
                ::send(client_fd, buf, r, 0);
            }
            // Reset under the lock before closing so a concurrent
            // stop() can't shutdown a recycled fd.
            {
                std::lock_guard<std::mutex> lk(_mu);
                _client_fd = -1;
            }
            ::close(client_fd);
        });
    }

    void stop() {
        _stop = true;
        // Shutdown the accepted connection so a parked recv() returns.
        // Closing _listen_fd alone leaves recv blocked on a different fd.
        {
            std::lock_guard<std::mutex> lk(_mu);
            if (_client_fd >= 0) {
                ::shutdown(_client_fd, SHUT_RDWR);
            }
        }
        if (_listen_fd >= 0) {
            ::close(_listen_fd);
            _listen_fd = -1;
        }
        if (_thread.joinable()) _thread.join();
    }

private:
    int               _listen_fd = -1;
    int               _client_fd = -1;
    std::mutex        _mu;
    uint16_t          _port      = 0;
    std::atomic<bool> _stop{false};
    std::thread       _thread;
};

// Loopback delivery is fast but read() is non-blocking, so a tight
// retry loop with tiny sleeps keeps tests stable without depending on
// kernel scheduling.
int await_read(PosixTcpTransport& t, uint8_t* dst, size_t n) {
    for (int i = 0; i < 200; ++i) {
        const int r = t.read(dst, n);
        if (r != 0) return r;
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    return 0;
}

}  // namespace

TEST_CASE("Fresh transport is not connected", "[tcp_transport]") {
    PosixTcpTransport t;
    CHECK_FALSE(t.is_connected());

    uint8_t buf[8] = {};
    CHECK(t.read(buf, sizeof(buf)) == -1);
    const uint8_t one = 1;
    CHECK(t.write(&one, 1) < 0);
}

TEST_CASE("connect to a port with no listener fails", "[tcp_transport]") {
    PosixTcpTransport t;
    // Loopback port 1 has no listener; connect() should refuse or
    // time out. Either way the transport stays disconnected.
    CHECK_FALSE(t.connect(LOOPBACK_BE, 1));
    CHECK_FALSE(t.is_connected());
}

TEST_CASE("connect/echo round trip", "[tcp_transport]") {
    TcpEchoServer server;
    server.start();
    PosixTcpTransport t;
    REQUIRE(t.connect(LOOPBACK_BE, server.port()));
    CHECK(t.is_connected());

    const uint8_t payload[] = {0xDE, 0xAD, 0xBE, 0xEF};
    // A few bytes into a fresh socket's empty send buffer go whole.
    REQUIRE(t.write(payload, sizeof(payload)) ==
            static_cast<int>(sizeof(payload)));

    uint8_t buf[8] = {};
    const int r = await_read(t, buf, sizeof(buf));
    REQUIRE(r == static_cast<int>(sizeof(payload)));
    CHECK(std::memcmp(buf, payload, sizeof(payload)) == 0);
}

TEST_CASE("connect is idempotent while already connected", "[tcp_transport]") {
    TcpEchoServer server;
    server.start();
    PosixTcpTransport t;
    REQUIRE(t.connect(LOOPBACK_BE, server.port()));
    CHECK(t.connect(LOOPBACK_BE, server.port()));
    CHECK(t.is_connected());
}

TEST_CASE("disconnect is idempotent", "[tcp_transport]") {
    TcpEchoServer server;
    server.start();
    PosixTcpTransport t;
    REQUIRE(t.connect(LOOPBACK_BE, server.port()));
    t.disconnect();
    CHECK_FALSE(t.is_connected());
    t.disconnect();  // no crash
    CHECK_FALSE(t.is_connected());
}

TEST_CASE("read returns < 0 after peer closes", "[tcp_transport]") {
    TcpEchoServer server;
    server.start();
    PosixTcpTransport t;
    REQUIRE(t.connect(LOOPBACK_BE, server.port()));
    server.stop();

    uint8_t buf[8] = {};
    int r = 0;
    // FIN delivery is near-instant on loopback but not zero-latency;
    // give the read a few ticks before declaring the test broken.
    for (int i = 0; i < 200; ++i) {
        r = t.read(buf, sizeof(buf));
        if (r < 0) break;
        std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    CHECK(r < 0);
    CHECK_FALSE(t.is_connected());
}

TEST_CASE("write fails after disconnect", "[tcp_transport]") {
    TcpEchoServer server;
    server.start();
    PosixTcpTransport t;
    REQUIRE(t.connect(LOOPBACK_BE, server.port()));
    t.disconnect();
    const uint8_t one = 1;
    CHECK(t.write(&one, 1) < 0);
}

TEST_CASE("a large buffer drains through repeated partial writes",
          "[tcp_transport]") {
    TcpEchoServer server;
    server.start();
    PosixTcpTransport t;
    REQUIRE(t.connect(LOOPBACK_BE, server.port()));

    // 64 KB is larger than most kernels' default per-socket send/recv
    // buffers, so this exercises partial acceptance (and 0 = "buffer
    // full") under real backpressure while the echo server drains.
    constexpr size_t kBig = 64 * 1024;
    std::vector<uint8_t> tx(kBig);
    for (size_t i = 0; i < kBig; ++i) tx[i] = static_cast<uint8_t>(i);

    size_t sent = 0;
    for (int i = 0; i < 10'000 && sent < kBig; ++i) {
        const int w = t.write(tx.data() + sent, kBig - sent);
        REQUIRE(w >= 0);
        if (w == 0) {
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
            continue;
        }
        sent += static_cast<size_t>(w);
    }
    REQUIRE(sent == kBig);

    std::vector<uint8_t> rx;
    rx.reserve(kBig);
    uint8_t buf[1024];
    while (rx.size() < kBig) {
        const int r = await_read(t, buf, sizeof(buf));
        if (r <= 0) break;
        rx.insert(rx.end(), buf, buf + r);
    }
    REQUIRE(rx.size() == kBig);
    CHECK(std::memcmp(rx.data(), tx.data(), kBig) == 0);
}

TEST_CASE("write reports a full buffer instead of blocking",
          "[tcp_transport]") {
    // A listener that never accepts: the connection completes via the
    // backlog, but nobody drains the peer side, so the client's send
    // buffer eventually fills for good.
    const int listen_fd = ::socket(AF_INET, SOCK_STREAM, 0);
    REQUIRE(listen_fd >= 0);
    sockaddr_in addr{};
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port        = htons(0);
    REQUIRE(::bind(listen_fd, reinterpret_cast<sockaddr*>(&addr),
                   sizeof(addr)) == 0);
    REQUIRE(::listen(listen_fd, 1) == 0);
    sockaddr_in actual{};
    socklen_t alen = sizeof(actual);
    REQUIRE(::getsockname(listen_fd, reinterpret_cast<sockaddr*>(&actual),
                          &alen) == 0);

    PosixTcpTransport t;
    REQUIRE(t.connect(LOOPBACK_BE, ntohs(actual.sin_port)));

    std::vector<uint8_t> chunk(64 * 1024, 0x55);
    bool saw_full = false;
    // Buffers on loopback can be large; cap the attempts, not the time.
    for (int i = 0; i < 1'000; ++i) {
        const auto start = std::chrono::steady_clock::now();
        const int w = t.write(chunk.data(), chunk.size());
        const auto elapsed = std::chrono::steady_clock::now() - start;
        // Non-blocking: any single call must return promptly, far
        // under the old 500 ms all-or-fail deadline.
        CHECK(elapsed < std::chrono::milliseconds(200));
        REQUIRE(w >= 0);
        if (w == 0) {
            saw_full = true;
            break;
        }
    }
    CHECK(saw_full);
    CHECK(t.is_connected());

    ::close(listen_fd);
}
