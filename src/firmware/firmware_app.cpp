#include "firmware_app.h"

#include <Arduino.h>
#include <Esp.h>

#include "diagnostics.h"
#include "wire_constants.h"

namespace firmware {
namespace {

static constexpr uint32_t kDetachGraceHoldMs = 5000;

}  // namespace

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
    log_line("[mode] %s", mode_name_());

    _background_store.begin();
    _discovery.begin(_identity);
    _connection.begin(_device, _identity, _background_store);
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

    if (_reboot_pending) {
        maybe_log_status(
            _last_status_ms,
            mode_name_(),
            _wifi.snapshot(),
            _discovery.snapshot(),
            _connection.snapshot()
        );
        reboot_if_due();
        delay(kLoopDelayMs);
        return;
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
            maybe_log_status(
                _last_status_ms,
                mode_name_(),
                _wifi.snapshot(),
                _discovery.snapshot(),
                _connection.snapshot()
            );
            reboot_if_due();
            delay(kLoopDelayMs);
            return;
        }
    }

    _discovery.poll();

    if (_connection.is_attached()) {
        enter_attached_controlled_();
        _device.tick_once();
    } else {
        tick_detached_mode_();
    }

    maybe_log_status(
        _last_status_ms,
        mode_name_(),
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
    if (_mode == DeviceMode::attached_controlled) {
        enter_detached_grace_hold_(reason);
    }
}

void FirmwareApp::enter_attached_controlled_()
{
    if (_mode == DeviceMode::attached_controlled) {
        return;
    }
    _mode = DeviceMode::attached_controlled;
    _detach_hold_deadline_ms = 0;
    log_line("[mode] attached_controlled");
}

void FirmwareApp::enter_detached_grace_hold_(const char* reason)
{
    if (_mode != DeviceMode::attached_controlled) {
        return;
    }
    _mode = DeviceMode::detached_grace_hold;
    _detach_hold_deadline_ms = millis() + kDetachGraceHoldMs;
    if (reason != nullptr && reason[0] != '\0') {
        log_line("[mode] detached_grace_hold reason=%s", reason);
    } else {
        log_line("[mode] detached_grace_hold");
    }
}

void FirmwareApp::enter_detached_blank_(const char* reason)
{
    if (_mode == DeviceMode::detached_blank) {
        return;
    }
    _device.present_black_frame();
    _mode = DeviceMode::detached_blank;
    _detach_hold_deadline_ms = 0;
    if (reason != nullptr && reason[0] != '\0') {
        log_line("[mode] detached_blank reason=%s", reason);
    } else {
        log_line("[mode] detached_blank");
    }
}

void FirmwareApp::tick_detached_mode_()
{
    if (_mode != DeviceMode::detached_grace_hold) {
        return;
    }
    const uint32_t now = millis();
    if (static_cast<int32_t>(now - _detach_hold_deadline_ms) < 0) {
        return;
    }
    enter_detached_blank_("grace expired");
}

const char* FirmwareApp::mode_name_() const
{
    switch (_mode) {
        case DeviceMode::attached_controlled:
            return "attached_controlled";
        case DeviceMode::detached_grace_hold:
            return "detached_grace_hold";
        case DeviceMode::detached_blank:
            return "detached_blank";
    }
    return "unknown";
}

void FirmwareApp::schedule_reboot()
{
    if (_reboot_pending) {
        return;
    }
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
