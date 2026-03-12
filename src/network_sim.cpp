// network_sim — Standalone TCP/UDP transport wrapper for ESPSimulated.
//
// Listens for a single TCP connection from a Python controller, dispatches
// commands to an ESPSimulated instance, and sends RGB frames back over UDP.
//
// Usage:
//   ./network_sim --device-uid UID [--tcp-port PORT]
//                 [--discovery-port PORT] [--discovery-host HOST]
// Discovery is mandatory. Runtime config (device_id, strip_length, frame_port)
// is provided by the controller via CMD_CONFIGURE after TCP connect.

#include "esp_simulated.h"

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <csignal>
#include <unistd.h>
#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <fcntl.h>
#include <memory>
#include <string>
#include <vector>

// Wire protocol command types (must match controller/elemctl/wire.py)
static constexpr uint8_t CMD_CONFIGURE  = 0x04;
static constexpr uint8_t CMD_LOAD       = 0x10;
static constexpr uint8_t CMD_START      = 0x11;
static constexpr uint8_t CMD_JUMP       = 0x12;
static constexpr uint8_t CMD_PAUSE      = 0x13;
static constexpr uint8_t CMD_RESUME     = 0x14;
static constexpr uint8_t CMD_STOP       = 0x15;
static constexpr uint8_t CMD_DEBUG_SEEK = 0x22;
static constexpr uint8_t CMD_DEBUG_STEP = 0x23;
static constexpr uint8_t CMD_ACK        = 0x80;

static volatile sig_atomic_t g_running = 1;

static void signal_handler(int) { g_running = 0; }

struct TransportState {
    uint16_t device_id;
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Blocking send — temporarily clears O_NONBLOCK so the kernel handles
// back-pressure instead of busy-spinning on EAGAIN.
static bool send_all(int fd, const void* data, size_t len)
{
    int flags = fcntl(fd, F_GETFL);
    bool was_nonblock = (flags >= 0) && (flags & O_NONBLOCK);
    if (was_nonblock && fcntl(fd, F_SETFL, flags & ~O_NONBLOCK) < 0)
        return false;

    const uint8_t* p = static_cast<const uint8_t*>(data);
    bool ok = true;
    while (len > 0) {
        ssize_t n = send(fd, p, len, 0);
        if (n > 0) {
            p += n;
            len -= (size_t)n;
        } else if (n < 0 && errno == EINTR) {
            continue;
        } else {
            ok = false;
            break;
        }
    }

    if (was_nonblock)
        fcntl(fd, F_SETFL, flags);
    return ok;
}

static void send_ack(int tcp_fd, uint8_t status)
{
    uint8_t buf[6];
    uint32_t len = 2; // type + status
    memcpy(buf, &len, 4);
    buf[4] = CMD_ACK;
    buf[5] = status;
    send_all(tcp_fd, buf, 6);
}

// ---------------------------------------------------------------------------
// Discovery HELLO packet
// ---------------------------------------------------------------------------

static constexpr uint16_t DISCOVERY_MAGIC = 0x454C;

static std::vector<uint8_t> build_hello_packet(const std::string& uid, uint16_t tcp_port)
{
    std::vector<uint8_t> pkt(5 + uid.size());
    uint16_t magic = DISCOVERY_MAGIC;
    memcpy(pkt.data(), &magic, 2);
    memcpy(pkt.data() + 2, &tcp_port, 2);
    pkt[4] = (uint8_t)uid.size();
    memcpy(pkt.data() + 5, uid.data(), uid.size());
    return pkt;
}

// ---------------------------------------------------------------------------
// TCP command parsing
// ---------------------------------------------------------------------------

static constexpr size_t TCP_BUF_INITIAL = 32768;
// 4 MiB — large enough for any realistic blob, small enough to reject garbage.
static constexpr size_t TCP_MSG_MAX = 4 * 1024 * 1024;

// Returns: 0 = ok, -1 = connection closed/error
static int poll_tcp_commands(int tcp_fd,
                             std::unique_ptr<ESPSimulated>& device,
                             TransportState& state,
                             sockaddr_in& controller_addr,
                             bool& configured,
                             std::vector<uint8_t>& buf, size_t& buf_used)
{
    // Ensure room for at least one recv chunk
    if (buf.size() - buf_used < 4096)
        buf.resize(buf.size() * 2);

    ssize_t n = recv(tcp_fd, buf.data() + buf_used, buf.size() - buf_used, MSG_DONTWAIT);
    if (n == 0) return -1; // connection closed
    if (n < 0) {
        if (errno == EAGAIN || errno == EWOULDBLOCK)
            n = 0; // no data available
        else
            return -1; // real error
    }
    buf_used += (size_t)n;

    // Process complete messages
    while (buf_used >= 4) {
        uint32_t msg_len;
        memcpy(&msg_len, buf.data(), 4);

        // Validate: msg_len must be >= 1 (at least the type byte)
        if (msg_len < 1) {
            // Invalid frame — consume the 4-byte header and resync
            buf_used -= 4;
            if (buf_used > 0)
                memmove(buf.data(), buf.data() + 4, buf_used);
            continue;
        }

        // Reject absurdly large messages to prevent OOM from corrupted headers
        if (msg_len > TCP_MSG_MAX) {
            fprintf(stderr, "network_sim: message too large (%u bytes), closing connection\n", msg_len);
            return -1;
        }

        size_t total = 4 + (size_t)msg_len;
        if (buf_used < total) {
            // Incomplete message — grow buffer if needed to fit it
            if (buf.size() < total)
                buf.resize(total);
            break;
        }

        uint8_t cmd_type = buf[4];
        const uint8_t* payload = buf.data() + 5;
        uint32_t payload_len = msg_len - 1;

        switch (cmd_type) {
        case CMD_CONFIGURE: {
            if (configured) { send_ack(tcp_fd, 1); break; }
            if (payload_len < 6) { send_ack(tcp_fd, 1); break; }
            uint16_t device_id, strip_length, frame_port;
            memcpy(&device_id, payload, 2);
            memcpy(&strip_length, payload + 2, 2);
            memcpy(&frame_port, payload + 4, 2);
            if (strip_length < 1 || frame_port < 1) {
                send_ack(tcp_fd, 1);
                break;
            }
            device = std::make_unique<ESPSimulated>(strip_length);
            state.device_id = device_id;
            controller_addr.sin_port = htons(frame_port);
            configured = true;
            send_ack(tcp_fd, 0);
            break;
        }
        case CMD_LOAD: {
            if (!configured || !device) { send_ack(tcp_fd, 2); break; }
            if (payload_len < 4) { send_ack(tcp_fd, 1); break; }
            uint16_t dev_id, gen;
            memcpy(&dev_id, payload, 2);
            memcpy(&gen, payload + 2, 2);
            state.device_id = dev_id;
            const uint8_t* blob = payload + 4;
            size_t blob_len = payload_len - 4;
            bool ok = device->handle_load(blob, blob_len, gen);
            send_ack(tcp_fd, ok ? 0 : 1);
            break;
        }
        case CMD_START: {
            if (!configured || !device) break;
            if (payload_len < 8) break;
            int64_t t0;
            memcpy(&t0, payload, 8);
            device->handle_start(t0);
            break;
        }
        case CMD_JUMP: {
            if (!configured || !device) break;
            if (payload_len < 14) break;
            int64_t t0;
            float t_rel;
            uint16_t gen;
            memcpy(&t0, payload, 8);
            memcpy(&t_rel, payload + 8, 4);
            memcpy(&gen, payload + 12, 2);
            device->handle_jump(t0, t_rel, gen);
            break;
        }
        case CMD_PAUSE:
            if (configured && device) device->handle_pause();
            break;
        case CMD_RESUME: {
            if (!configured || !device) break;
            if (payload_len < 8) break;
            int64_t t0;
            memcpy(&t0, payload, 8);
            device->handle_resume(t0);
            break;
        }
        case CMD_STOP:
            if (configured && device) device->handle_stop();
            break;
        case CMD_DEBUG_SEEK: {
            if (!configured || !device) break;
            if (payload_len < 4) break;
            float t_rel;
            memcpy(&t_rel, payload, 4);
            device->debug_seek(t_rel);
            break;
        }
        case CMD_DEBUG_STEP: {
            if (!configured || !device) break;
            if (payload_len < 1) break;
            int8_t direction = (int8_t)payload[0];
            device->debug_step(direction);
            break;
        }
        default:
            // Unknown command — skip
            break;
        }

        // Consume processed message
        buf_used -= total;
        if (buf_used > 0)
            memmove(buf.data(), buf.data() + total, buf_used);
    }

    return 0;
}

// ---------------------------------------------------------------------------
// UDP frame sending
// ---------------------------------------------------------------------------

static void send_frames(int udp_fd, const sockaddr_in& controller_addr,
                        const TransportState& state, ESPSimulated& device)
{
    auto frames = device.drain_frames();
    for (auto& f : frames) {
        // Header: device_id(u16) + gen(u16) + frame_index(u32) + t_rel(f32) = 12 bytes
        size_t pkt_size = 12 + f.rgb.size();
        std::vector<uint8_t> pkt(pkt_size);
        memcpy(pkt.data(), &state.device_id, 2);
        memcpy(pkt.data() + 2, &f.gen, 2);
        memcpy(pkt.data() + 4, &f.frame_index, 4);
        memcpy(pkt.data() + 8, &f.t_rel, 4);
        memcpy(pkt.data() + 12, f.rgb.data(), f.rgb.size());
        sendto(udp_fd, pkt.data(), pkt.size(), 0,
               (const sockaddr*)&controller_addr, sizeof(controller_addr));
    }
}

// ---------------------------------------------------------------------------
// CLI argument parsing
// ---------------------------------------------------------------------------

static constexpr int DEFAULT_DISCOVERY_PORT = 6040;

struct Args {
    int tcp_port = 0;
    int discovery_port = DEFAULT_DISCOVERY_PORT;
    std::string discovery_host = "127.0.0.1";
    std::string device_uid;
};

static bool parse_args(int argc, char** argv, Args& args)
{
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--tcp-port") == 0 && i + 1 < argc) {
            args.tcp_port = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--discovery-port") == 0 && i + 1 < argc) {
            args.discovery_port = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--discovery-host") == 0 && i + 1 < argc) {
            args.discovery_host = argv[++i];
        } else if (strcmp(argv[i], "--device-uid") == 0 && i + 1 < argc) {
            args.device_uid = argv[++i];
        } else {
            fprintf(stderr, "Unknown argument: %s\n", argv[i]);
            return false;
        }
    }

    if (args.device_uid.empty()) {
        fprintf(stderr,
                "Usage: %s --device-uid UID [--tcp-port PORT] [--discovery-port PORT] "
                "[--discovery-host HOST]\n",
                argv[0]);
        return false;
    }

    // tcp_port == 0 is valid (ephemeral bind), but other ports must be 1-65535
    if (args.tcp_port < 0 || args.tcp_port > 65535) {
        fprintf(stderr, "--tcp-port must be 0-65535\n");
        return false;
    }
    if (args.discovery_port < 1 || args.discovery_port > 65535) {
        fprintf(stderr, "--discovery-port must be 1-65535\n");
        return false;
    }

    return true;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

// Monotonic clock in microseconds
static int64_t now_us()
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000000 + ts.tv_nsec / 1000;
}

static void send_hello(int udp_fd, const sockaddr_in& dest,
                       const std::vector<uint8_t>& pkt)
{
    sendto(udp_fd, pkt.data(), pkt.size(), 0,
           (const sockaddr*)&dest, sizeof(dest));
}

int main(int argc, char** argv)
{
    Args args;
    if (!parse_args(argc, argv, args))
        return 1;

    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    std::unique_ptr<ESPSimulated> device;
    TransportState state{0};

    // TCP server socket (non-blocking for accept)
    int tcp_server = socket(AF_INET, SOCK_STREAM, 0);
    if (tcp_server < 0) { perror("socket(tcp)"); return 1; }

    int opt = 1;
    setsockopt(tcp_server, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    sockaddr_in srv_addr{};
    srv_addr.sin_family = AF_INET;
    srv_addr.sin_addr.s_addr = INADDR_ANY;
    srv_addr.sin_port = htons((uint16_t)args.tcp_port);

    if (bind(tcp_server, (sockaddr*)&srv_addr, sizeof(srv_addr)) < 0) {
        perror("bind(tcp)"); close(tcp_server); return 1;
    }

    // If port 0 was requested, read back the assigned port
    if (args.tcp_port == 0) {
        socklen_t addr_len = sizeof(srv_addr);
        getsockname(tcp_server, (sockaddr*)&srv_addr, &addr_len);
        args.tcp_port = ntohs(srv_addr.sin_port);
    }

    if (listen(tcp_server, 1) < 0) {
        perror("listen"); close(tcp_server); return 1;
    }

    // Set TCP server non-blocking so we can interleave accept with HELLO sends
    fcntl(tcp_server, F_SETFL, fcntl(tcp_server, F_GETFL) | O_NONBLOCK);

    // UDP socket (unbound, for sendto frames + optionally HELLO)
    int udp_fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (udp_fd < 0) { perror("socket(udp)"); close(tcp_server); return 1; }

    // Discovery setup
    std::vector<uint8_t> hello_pkt;
    sockaddr_in discovery_addr{};
    opt = 1;
    setsockopt(udp_fd, SOL_SOCKET, SO_BROADCAST, &opt, sizeof(opt));
    hello_pkt = build_hello_packet(args.device_uid, (uint16_t)args.tcp_port);
    discovery_addr.sin_family = AF_INET;
    discovery_addr.sin_port = htons((uint16_t)args.discovery_port);
    if (inet_pton(AF_INET, args.discovery_host.c_str(), &discovery_addr.sin_addr) != 1) {
        fprintf(stderr, "invalid --discovery-host: %s\n", args.discovery_host.c_str());
        close(udp_fd); close(tcp_server); return 1;
    }

    // TCP read buffer (grows dynamically for large blobs)
    std::vector<uint8_t> tcp_buf(TCP_BUF_INITIAL);
    size_t tcp_buf_used = 0;

    printf("network_sim: listening on tcp=%d\n", args.tcp_port);
    printf("network_sim: discovery -> %s:%d uid=%s\n",
           args.discovery_host.c_str(), args.discovery_port, args.device_uid.c_str());
    fflush(stdout);

    static constexpr int64_t HELLO_INTERVAL_US = 500000; // 500ms
    int64_t last_hello_us = 0;

    while (g_running) {
        printf("Waiting for controller on port %d...\n", args.tcp_port);
        fflush(stdout);

        // Non-blocking accept loop — interleave with HELLO sends
        int tcp_fd = -1;
        while (g_running && tcp_fd < 0) {
            tcp_fd = accept(tcp_server, nullptr, nullptr);
            if (tcp_fd < 0) {
                if (errno != EAGAIN && errno != EWOULDBLOCK) {
                    perror("accept");
                }
                // Send HELLO if discovery is enabled
                int64_t now = now_us();
                if (now - last_hello_us >= HELLO_INTERVAL_US) {
                    send_hello(udp_fd, discovery_addr, hello_pkt);
                    last_hello_us = now;
                }
                usleep(100000); // 100ms between accept attempts
            }
        }
        if (!g_running) break;

        printf("Controller connected.\n");
        fflush(stdout);

        // Learn controller IP from accepted connection
        sockaddr_in peer{};
        socklen_t peer_len = sizeof(peer);
        getpeername(tcp_fd, (sockaddr*)&peer, &peer_len);

        sockaddr_in controller_addr{};
        controller_addr.sin_family = AF_INET;
        controller_addr.sin_port = 0;
        controller_addr.sin_addr = peer.sin_addr;
        bool configured = false;

        // Set TCP non-blocking for recv
        fcntl(tcp_fd, F_SETFL, O_NONBLOCK);

        tcp_buf_used = 0;

        while (g_running) {
            int rc = poll_tcp_commands(
                tcp_fd, device, state, controller_addr, configured, tcp_buf, tcp_buf_used
            );
            if (rc < 0) break; // connection closed

            if (device) {
                device->tick_once();
                send_frames(udp_fd, controller_addr, state, *device);
            }

            // Continue sending HELLOs during active connection (enables re-discovery)
            int64_t now = now_us();
            if (now - last_hello_us >= HELLO_INTERVAL_US) {
                send_hello(udp_fd, discovery_addr, hello_pkt);
                last_hello_us = now;
            }

            usleep(20000); // ~50fps
        }

        close(tcp_fd);
        printf("Controller disconnected.\n");
        fflush(stdout);

        device.reset();
        state.device_id = 0;
    }

    close(udp_fd);
    close(tcp_server);
    printf("network_sim: shutdown.\n");
    return 0;
}
