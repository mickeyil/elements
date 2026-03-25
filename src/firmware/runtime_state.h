#pragma once

#include <IPAddress.h>
#include <WString.h>

#include <cstddef>
#include <cstdint>
#include <vector>

namespace firmware {

struct TransportState {
    uint16_t device_id = 0;
    uint16_t frame_port = 0;
    IPAddress controller_ip;
};

struct WifiState {
    bool ready = false;
    bool server_started = false;
    bool duplicate_uid_rejected = false;
    bool preferences_ready = false;
    uint32_t last_retry_ms = 0;
    String last_good_ssid;
};

struct ControllerLinkState {
    bool configured = false;
    bool connected = false;
    uint16_t last_sync_seq = 0;
    std::vector<uint8_t> tcp_buf;
    size_t tcp_buf_used = 0;

    explicit ControllerLinkState(size_t initial_tcp_buf_size = 0)
        : tcp_buf(initial_tcp_buf_size)
    {
    }
};

struct DiagnosticsState {
    uint32_t last_hello_ms = 0;
    uint32_t last_status_ms = 0;
    uint32_t wifi_connect_attempts = 0;
    uint32_t wifi_connect_successes = 0;
    uint32_t hello_count = 0;
    uint32_t tcp_accept_count = 0;
    uint32_t tcp_disconnect_count = 0;
    uint32_t configure_count = 0;
    uint32_t load_count = 0;
    uint32_t start_count = 0;
    uint32_t jump_count = 0;
    uint32_t pause_count = 0;
    uint32_t resume_count = 0;
    uint32_t stop_count = 0;
    uint64_t frames_sent = 0;
    bool have_frame_stats = false;
    uint16_t last_frame_gen = 0;
    uint32_t last_frame_index = 0;
    float last_frame_t_rel = 0.0f;
};

struct RuntimeState {
    String device_uid;
    uint32_t boot_token = 0;
    bool reboot_pending = false;
    uint32_t reboot_deadline_ms = 0;
};

}  // namespace firmware
