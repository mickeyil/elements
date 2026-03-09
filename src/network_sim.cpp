// network_sim — Standalone TCP/UDP transport wrapper for ESPSimulated.
//
// Listens for a single TCP connection from a Python controller, dispatches
// commands to an ESPSimulated instance, and sends RGB frames back over UDP.
//
// Usage:
//   ./network_sim --tcp-port PORT --frame-port PORT --strip-length N [--device-id ID]

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
#include <vector>

// Wire protocol command types (must match controller/elemctl/wire.py)
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
// TCP command parsing
// ---------------------------------------------------------------------------

static constexpr size_t TCP_BUF_INITIAL = 32768;
// 4 MiB — large enough for any realistic blob, small enough to reject garbage.
static constexpr size_t TCP_MSG_MAX = 4 * 1024 * 1024;

// Returns: 0 = ok, -1 = connection closed/error
static int poll_tcp_commands(int tcp_fd, ESPSimulated& device,
                             TransportState& state,
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
        case CMD_LOAD: {
            if (payload_len < 4) { send_ack(tcp_fd, 1); break; }
            uint16_t dev_id, gen;
            memcpy(&dev_id, payload, 2);
            memcpy(&gen, payload + 2, 2);
            state.device_id = dev_id;
            const uint8_t* blob = payload + 4;
            size_t blob_len = payload_len - 4;
            bool ok = device.handle_load(blob, blob_len, gen);
            send_ack(tcp_fd, ok ? 0 : 1);
            break;
        }
        case CMD_START: {
            if (payload_len < 8) break;
            int64_t t0;
            memcpy(&t0, payload, 8);
            device.handle_start(t0);
            break;
        }
        case CMD_JUMP: {
            if (payload_len < 14) break;
            int64_t t0;
            float t_rel;
            uint16_t gen;
            memcpy(&t0, payload, 8);
            memcpy(&t_rel, payload + 8, 4);
            memcpy(&gen, payload + 12, 2);
            device.handle_jump(t0, t_rel, gen);
            break;
        }
        case CMD_PAUSE:
            device.handle_pause();
            break;
        case CMD_RESUME: {
            if (payload_len < 8) break;
            int64_t t0;
            memcpy(&t0, payload, 8);
            device.handle_resume(t0);
            break;
        }
        case CMD_STOP:
            device.handle_stop();
            break;
        case CMD_DEBUG_SEEK: {
            if (payload_len < 4) break;
            float t_rel;
            memcpy(&t_rel, payload, 4);
            device.debug_seek(t_rel);
            break;
        }
        case CMD_DEBUG_STEP: {
            if (payload_len < 1) break;
            int8_t direction = (int8_t)payload[0];
            device.debug_step(direction);
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

struct Args {
    int tcp_port = -1;
    int frame_port = -1;
    int strip_length = -1;
    int device_id = 0;
};

static bool parse_args(int argc, char** argv, Args& args)
{
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--tcp-port") == 0 && i + 1 < argc) {
            args.tcp_port = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--frame-port") == 0 && i + 1 < argc) {
            args.frame_port = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--strip-length") == 0 && i + 1 < argc) {
            args.strip_length = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--device-id") == 0 && i + 1 < argc) {
            args.device_id = atoi(argv[++i]);
        } else {
            fprintf(stderr, "Unknown argument: %s\n", argv[i]);
            return false;
        }
    }

    if (args.tcp_port < 0 || args.frame_port < 0 || args.strip_length < 1) {
        fprintf(stderr,
                "Usage: %s --tcp-port PORT --frame-port PORT --strip-length N "
                "[--device-id ID]\n",
                argv[0]);
        return false;
    }
    return true;
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

int main(int argc, char** argv)
{
    Args args;
    if (!parse_args(argc, argv, args))
        return 1;

    signal(SIGINT, signal_handler);
    signal(SIGTERM, signal_handler);

    auto device = std::make_unique<ESPSimulated>((uint16_t)args.strip_length);
    TransportState state{(uint16_t)args.device_id};

    // TCP server socket
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

    // UDP socket (unbound, for sendto)
    int udp_fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (udp_fd < 0) { perror("socket(udp)"); close(tcp_server); return 1; }

    // TCP read buffer (grows dynamically for large blobs)
    std::vector<uint8_t> tcp_buf(TCP_BUF_INITIAL);
    size_t tcp_buf_used = 0;

    printf("network_sim: listening on tcp=%d, frames->udp=%d, strip=%d, device_id=%d\n",
           args.tcp_port, args.frame_port, args.strip_length, args.device_id);
    fflush(stdout);

    while (g_running) {
        printf("Waiting for controller on port %d...\n", args.tcp_port);
        fflush(stdout);

        int tcp_fd = accept(tcp_server, nullptr, nullptr);
        if (tcp_fd < 0) {
            if (!g_running) break;
            perror("accept");
            continue;
        }

        printf("Controller connected.\n");
        fflush(stdout);

        // Learn controller IP from accepted connection
        sockaddr_in peer{};
        socklen_t peer_len = sizeof(peer);
        getpeername(tcp_fd, (sockaddr*)&peer, &peer_len);

        sockaddr_in controller_addr{};
        controller_addr.sin_family = AF_INET;
        controller_addr.sin_port = htons((uint16_t)args.frame_port);
        controller_addr.sin_addr = peer.sin_addr;

        // Set TCP non-blocking for recv
        fcntl(tcp_fd, F_SETFL, O_NONBLOCK);

        tcp_buf_used = 0;

        while (g_running) {
            int rc = poll_tcp_commands(tcp_fd, *device, state, tcp_buf, tcp_buf_used);
            if (rc < 0) break; // connection closed

            device->tick_once();
            send_frames(udp_fd, controller_addr, state, *device);

            usleep(20000); // ~50fps
        }

        close(tcp_fd);
        printf("Controller disconnected.\n");
        fflush(stdout);

        // Full reset for next connection — reconstruct the device so a
        // reconnecting controller starts from a clean IDLE state.
        device = std::make_unique<ESPSimulated>((uint16_t)args.strip_length);
        state.device_id = (uint16_t)args.device_id;
    }

    close(udp_fd);
    close(tcp_server);
    printf("network_sim: shutdown.\n");
    return 0;
}
