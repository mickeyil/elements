#include <arpa/inet.h>
#include <unistd.h>

#include <csignal>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "app.h"
#include "device_identity.h"
#include "discovery.h"
#include "hardware_profile_store.h"
#include "sim/file_key_value_store.h"
#include "sim/host_network_interface.h"
#include "sim/posix_file_store.h"
#include "sim/posix_tcp_transport.h"
#include "sim/posix_udp_transport.h"
#include "sim/sim_device_identity.h"
#include "sim/sim_frame_output.h"
#include "sim/sim_system_platform.h"
#include "slog.h"

// The sim device entry point: constructs the POSIX platform pieces,
// hands them to the shared App, and ticks it until a signal arrives.
// A reboot command exits with SIM_REBOOT_EXIT_CODE from inside
// SimSystemPlatform; the supervisor (elemctl sim) re-execs on that
// code, so this file never sees it.

namespace {

constexpr char DEFAULT_CONTROLLER_HOST[] = "127.0.0.1";
constexpr uint16_t DEFAULT_FRAME_PORT = 6042;

// Pause per loop pass. Frame pacing happens inside the App, so this
// only bounds idle CPU; it must stay well under a frame interval.
constexpr useconds_t TICK_SLEEP_US = 1000;

volatile std::sig_atomic_t g_stop = 0;

void handle_stop_signal(int)
{
    g_stop = 1;
}

struct SimOptions
{
    const char* device_uid = nullptr;
    const char* controller_host = DEFAULT_CONTROLLER_HOST;
    uint16_t frame_port = DEFAULT_FRAME_PORT;
};

void print_usage(const char* argv0)
{
    std::fprintf(stderr,
                 "usage: %s --device-uid <sim-...> [--controller-host <ipv4>] "
                 "[--frame-port <port>]\n",
                 argv0);
}

bool parse_args(int argc, char** argv, SimOptions& opts)
{
    for (int i = 1; i < argc; ++i) {
        const char* arg = argv[i];
        const char* value = (i + 1 < argc) ? argv[i + 1] : nullptr;

        if (std::strcmp(arg, "--device-uid") == 0 && value != nullptr) {
            opts.device_uid = value;
            ++i;
        } else if (std::strcmp(arg, "--controller-host") == 0 && value != nullptr) {
            opts.controller_host = value;
            ++i;
        } else if (std::strcmp(arg, "--frame-port") == 0 && value != nullptr) {
            char* end = nullptr;
            const long port = std::strtol(value, &end, 10);
            if (end == value || *end != '\0' || port < 1 || port > 65535) {
                std::fprintf(stderr, "bad --frame-port: %s\n", value);
                return false;
            }
            opts.frame_port = static_cast<uint16_t>(port);
            ++i;
        } else {
            std::fprintf(stderr, "unknown or incomplete argument: %s\n", arg);
            return false;
        }
    }

    if (opts.device_uid == nullptr || *opts.device_uid == '\0') {
        std::fprintf(stderr, "--device-uid is required\n");
        return false;
    }
    return true;
}

}  // namespace

int main(int argc, char** argv)
{
    SimOptions opts;
    if (!parse_args(argc, argv, opts)) {
        print_usage(argv[0]);
        return 2;
    }

    uint32_t controller_ip = 0;  // network byte order
    if (inet_pton(AF_INET, opts.controller_host, &controller_ip) != 1) {
        std::fprintf(stderr, "bad --controller-host: %s\n", opts.controller_host);
        return 2;
    }

    std::signal(SIGINT, handle_stop_signal);
    std::signal(SIGTERM, handle_stop_signal);

    const DeviceIdentity identity = make_sim_device_identity(opts.device_uid);

    HostNetworkInterface network;
    PosixUdpTransport discovery_udp;
    PosixUdpTransport sync_udp;
    PosixUdpTransport frame_udp;
    PosixUdpTransport log_udp;
    PosixTcpTransport tcp;
    PosixFileStore files(opts.device_uid);
    FileKeyValueStore profile_kv(opts.device_uid, HARDWARE_PROFILE_KV_NAMESPACE);
    SimSystemPlatform system;

    // Unicast discovery: broadcast does not reach a controller bound
    // on loopback, so the sim always aims DISCOVER at a known host.
    DiscoveryClient discovery(discovery_udp, identity, controller_ip);
    SimFrameOutput output(frame_udp, identity, controller_ip, opts.frame_port);

    App app(network, discovery, tcp, sync_udp, log_udp, files, profile_kv,
            system, output, identity);

    slog_info("sim device %s up; controller %s, frame port %u",
              identity.uid, opts.controller_host, opts.frame_port);

    app.begin();
    while (!g_stop) {
        app.tick();
        usleep(TICK_SLEEP_US);
    }

    slog_info("sim device %s stopped", identity.uid);
    return 0;
}
