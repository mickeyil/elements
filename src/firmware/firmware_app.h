#pragma once

#include <cstdint>

#include "controller_connection.h"
#include "device_identity.h"
#include "discovery_service.h"
#include "esp_device.h"
#include "wifi_manager.h"

namespace firmware {

enum class DeviceMode : uint8_t {
    attached_controlled,
    detached_grace_hold,
    detached_blank,
};

class FirmwareApp {
public:
    void begin();
    void run_once();

private:
    void on_network_down();
    void on_controller_disconnect();
    void invalidate_controller_runtime_(const char* reason, bool stop_transport);
    void enter_attached_controlled_();
    void enter_detached_grace_hold_(const char* reason);
    void enter_detached_blank_(const char* reason);
    void tick_detached_mode_();
    const char* mode_name_() const;
    void schedule_reboot();
    void reboot_if_due();

    WifiManager _wifi;
    DiscoveryService _discovery;
    ControllerConnection _connection;
    ESPDevice _device;
    DeviceIdentity _identity;
    uint32_t _last_status_ms = 0;
    DeviceMode _mode = DeviceMode::detached_blank;
    uint32_t _detach_hold_deadline_ms = 0;
    bool _reboot_pending = false;
    uint32_t _reboot_deadline_ms = 0;
};

}  // namespace firmware
