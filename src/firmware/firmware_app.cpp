#include "firmware_app.h"

#include <Arduino.h>
#include <Esp.h>

#include "diagnostics.h"
#include "wire_constants.h"

namespace firmware {

void FirmwareApp::begin()
{
    Serial.begin(115200);
    delay(200);

    esp_device_init_leds();
    _identity = read_device_identity();

    log_line("[boot] elements esp32 runtime starting");
    log_line("[boot] build=%s %s", __DATE__, __TIME__);
    log_line("[boot] uid=%s", _identity.uid.c_str());
    log_line("[boot] boot_token=%lu", static_cast<unsigned long>(_identity.boot_token));

    _discovery.begin(_identity);
    _connection.begin(_device, _identity);
    _wifi.begin();

    if (_wifi.is_ready()) {
        _discovery.start_if_needed();
        _connection.start_if_needed();
    }
}

void FirmwareApp::run_once()
{
    const WifiTransition wifi_transition = _wifi.poll();
    if (wifi_transition == WifiTransition::disconnected) {
        on_network_down();
    }

    if (_wifi.is_ready()) {
        _discovery.start_if_needed();
        _connection.start_if_needed();

        const ConnectionPollResult result = _connection.poll();
        if (result.disconnected) {
            on_controller_disconnect();
        }
        if (result.reboot_requested) {
            schedule_reboot();
        }
    }

    _discovery.poll();

    if (_connection.is_attached()) {
        _device.tick_once();
    }

    maybe_log_status(
        _last_status_ms,
        _wifi.snapshot(),
        _discovery.snapshot(),
        _connection.snapshot()
    );
    reboot_if_due();
    delay(kLoopDelayMs);
}

void FirmwareApp::on_network_down()
{
    _discovery.stop();
    invalidate_controller_runtime_("network down", true);
}

void FirmwareApp::on_controller_disconnect()
{
    invalidate_controller_runtime_(nullptr, false);
}

void FirmwareApp::invalidate_controller_runtime_(const char* reason, bool stop_transport)
{
    if (stop_transport) {
        _connection.stop(reason);
    }
    _device.reset_for_detach();
}

void FirmwareApp::schedule_reboot()
{
    _reboot_pending = true;
    _reboot_deadline_ms = millis() + kRebootDelayMs;
    log_line("[sys] reboot scheduled");
}

void FirmwareApp::reboot_if_due()
{
    if (!_reboot_pending) {
        return;
    }

    const uint32_t now = millis();
    if (static_cast<int32_t>(now - _reboot_deadline_ms) < 0) {
        return;
    }

    log_line("[sys] rebooting now");
    _connection.flush_active_client();
    delay(20);
    _connection.stop("reboot");
    delay(20);
    ESP.restart();
}

}  // namespace firmware
