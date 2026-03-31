#pragma once

#include <WiFi.h>
#include <cstddef>
#include <cstdint>
#include <vector>

#include "background_store.h"
#include "device_mode.h"
#include "device_identity.h"
#include "hardware_profile.h"

class ESPDevice;

namespace firmware {

enum class ConnectionState {
    stopped,
    listening,
    client_connected,
    attached,
};

struct ConnectionPollResult {
    bool disconnected = false;
    bool reboot_requested = false;
};

struct ConnectionSnapshot {
    ConnectionState state = ConnectionState::stopped;
    uint16_t device_id = 0;
    uint16_t frame_port = 0;
    IPAddress controller_ip;
    uint16_t last_sync_seq = 0;
    uint32_t tcp_accept_count = 0;
    uint32_t tcp_disconnect_count = 0;
    uint32_t load_count = 0;
    uint32_t start_count = 0;
    uint32_t stop_count = 0;
};

class ControllerConnection {
public:
    ControllerConnection();

    void begin(
        ESPDevice& device,
        const DeviceIdentity& identity,
        BackgroundStore& background_store,
        const DeviceMode* mode
    );
    void start_if_needed();
    void flush_active_client();
    void stop(const char* reason = nullptr);
    ConnectionPollResult poll();
    bool is_attached() const;
    ConnectionSnapshot snapshot() const;

private:
    int poll_commands_(ConnectionPollResult& result);
    void accept_client_();
    void disconnect_client_(const char* reason = nullptr);
    void reset_session_state_();
    bool has_active_client_() const;
    bool send_all_(const uint8_t* data, size_t len);
    bool send_ack_(uint8_t status);
    bool send_ack_(uint8_t status, const uint8_t* payload, size_t payload_len);
    uint8_t apply_profile_(const HardwareProfile& profile);
    uint8_t attach_(uint16_t device_id, uint16_t frame_port);

    bool handle_set_profile_(const uint8_t* payload, uint32_t payload_len);
    bool handle_attach_(const uint8_t* payload, uint32_t payload_len);
    void handle_sync_result_(const uint8_t* payload, uint32_t payload_len);
    bool handle_load_(const uint8_t* payload, uint32_t payload_len);
    bool handle_store_background_(const uint8_t* payload, uint32_t payload_len);
    bool handle_clear_background_();
    bool handle_query_device_status_();
    void handle_start_(const uint8_t* payload, uint32_t payload_len);
    void handle_jump_(const uint8_t* payload, uint32_t payload_len);
    void handle_pause_();
    void handle_resume_(const uint8_t* payload, uint32_t payload_len);
    void handle_stop_();
    bool handle_reboot_(ConnectionPollResult& result);

    const DeviceIdentity* _identity = nullptr;
    ESPDevice* _device = nullptr;
    BackgroundStore* _background_store = nullptr;
    const DeviceMode* _mode = nullptr;
    WiFiServer _tcp_server;
    WiFiClient _tcp_client;
    ConnectionState _state = ConnectionState::stopped;
    uint16_t _device_id = 0;
    uint16_t _frame_port = 0;
    IPAddress _controller_ip;
    uint16_t _last_sync_seq = 0;
    std::vector<uint8_t> _tcp_buf;
    size_t _tcp_buf_used = 0;
    uint32_t _tcp_accept_count = 0;
    uint32_t _tcp_disconnect_count = 0;
    uint32_t _load_count = 0;
    uint32_t _start_count = 0;
    uint32_t _stop_count = 0;
};

}  // namespace firmware
