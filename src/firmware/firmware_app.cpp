#include "firmware_app.h"

#include <Arduino.h>
#include <Esp.h>

#include <memory>
#include <new>

#include "diagnostics.h"
#include "hardware_profile.h"
#include "wire_constants.h"

namespace {

static constexpr uint32_t kDetachGraceHoldMs = 5000;
static constexpr char kProfileStripLengthKey[] = "prof_len";

}  // namespace

void FirmwareApp::begin()
{
    Serial.begin(115200);
    delay(200);

    esp_device_init_leds();
    _identity = read_device_identity();

    log_line("[boot] elements esp32 runtime starting");
    log_line("[boot] build=%s %s", __DATE__, __TIME__);
    log_line("[boot] uid=%s", _identity.uid);
    log_line("[boot] boot_token=%lu", static_cast<unsigned long>(_identity.boot_token));
    log_line("[mode] %s", mode_name_());

    _profile_preferences_ready = _profile_preferences.begin(kNvsNamespace, false);
    if (!_profile_preferences_ready) {
        log_line("[profile] prefs unavailable");
    }
    load_persisted_profile_();

    _background_store.begin();
    _discovery.begin(_identity);
    _connection.begin(_device, _identity, _background_store, &_mode);
    _wifi.begin();

    if (_background_store.metadata().present) {
        if (try_start_background_()) {
            enter_detached_background_();
        } else {
            log_line("[bg] startup skipped or failed");
        }
    }

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
        persist_profile_if_needed_();
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
    if (_mode == DeviceMode::detached_background) {
        return;
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

void FirmwareApp::enter_detached_background_()
{
    if (_mode == DeviceMode::detached_background) {
        return;
    }
    _mode = DeviceMode::detached_background;
    _detach_hold_deadline_ms = 0;
    log_line("[mode] detached_background");
}

void FirmwareApp::tick_detached_mode_()
{
    if (_mode == DeviceMode::detached_grace_hold) {
        const uint32_t now = millis();
        if (static_cast<int32_t>(now - _detach_hold_deadline_ms) < 0) {
            return;
        }
        enter_detached_blank_("grace expired");
        if (try_start_background_()) {
            enter_detached_background_();
        }
        return;
    }

    if (_mode != DeviceMode::detached_background) {
        return;
    }

    if (_device.tick_once()) {
        return;
    }

    if (_device.state() == DeviceState::ENDED) {
        log_line("[bg] restarting local background");
        _device.handle_start(0);
        return;
    }

    log_line("[bg] background runtime left detached mode unexpectedly");
    enter_detached_blank_("background runtime unexpected state");
}

bool FirmwareApp::try_start_background_()
{
    const BackgroundMetadata meta = _background_store.metadata();
    if (!meta.present) {
        return false;
    }

    if (_device.has_hardware_profile()) {
        if (_device.strip_length() != meta.strip_length) {
            log_line(
                "[bg] profile mismatch current=%u stored=%u",
                static_cast<unsigned>(_device.strip_length()),
                static_cast<unsigned>(meta.strip_length)
            );
            return false;
        }
    } else {
        if (!_device.apply_hardware_profile(HardwareProfile(meta.strip_length))) {
            log_line(
                "[bg] failed to apply stored profile strip_length=%u",
                static_cast<unsigned>(meta.strip_length)
            );
            return false;
        }
        log_line(
            "[bg] applied stored profile strip_length=%u",
            static_cast<unsigned>(meta.strip_length)
        );
        persist_profile_if_needed_();
    }

    std::unique_ptr<uint8_t[]> blob(new (std::nothrow) uint8_t[meta.blob_len]);
    if (!blob) {
        log_line(
            "[bg] failed to allocate %lu bytes for background",
            static_cast<unsigned long>(meta.blob_len)
        );
        return false;
    }
    if (!_background_store.read_blob(blob.get(), meta.blob_len)) {
        log_line("[bg] failed to read stored background");
        return false;
    }
    if (!_device.handle_load(blob.get(), meta.blob_len, 0)) {
        log_line("[bg] failed to load stored background");
        return false;
    }
    if (_device.state() != DeviceState::LOADED) {
        log_line("[bg] stored background left device in unexpected state after load");
        return false;
    }

    _device.handle_start(0);
    if (_device.state() != DeviceState::PLAYING) {
        log_line("[bg] failed to start stored background");
        return false;
    }

    log_line(
        "[bg] started strip_length=%u bytes=%lu crc32=%08lx",
        static_cast<unsigned>(meta.strip_length),
        static_cast<unsigned long>(meta.blob_len),
        static_cast<unsigned long>(meta.crc32)
    );
    return true;
}

bool FirmwareApp::load_persisted_profile_()
{
    if (!_profile_preferences_ready) {
        return false;
    }

    const uint16_t strip_length = _profile_preferences.getUShort(kProfileStripLengthKey, 0);
    if (strip_length == 0) {
        log_line("[profile] none");
        _handled_profile_present = false;
        _handled_profile_strip_length = 0;
        return false;
    }

    _handled_profile_present = true;
    _handled_profile_strip_length = strip_length;

    const HardwareProfile profile(strip_length);
    if (!profile.is_valid()) {
        log_line(
            "[profile] invalid stored strip_length=%u",
            static_cast<unsigned>(strip_length)
        );
        return false;
    }
    if (!_device.apply_hardware_profile(profile)) {
        log_line(
            "[profile] apply failed strip_length=%u",
            static_cast<unsigned>(strip_length)
        );
        return false;
    }
    log_line(
        "[profile] loaded strip_length=%u",
        static_cast<unsigned>(strip_length)
    );
    return true;
}

void FirmwareApp::persist_profile_if_needed_()
{
    if (!_device.has_hardware_profile()) {
        return;
    }

    const uint16_t strip_length = _device.strip_length();
    if (_handled_profile_present && strip_length == _handled_profile_strip_length) {
        return;
    }

    if (_profile_preferences_ready) {
        if (_profile_preferences.putUShort(kProfileStripLengthKey, strip_length)) {
            log_line(
                "[profile] persisted strip_length=%u",
                static_cast<unsigned>(strip_length)
            );
        } else {
            log_line(
                "[profile] persist failed strip_length=%u (continuing)",
                static_cast<unsigned>(strip_length)
            );
        }
    } else {
        log_line(
            "[profile] persist skipped strip_length=%u (prefs unavailable)",
            static_cast<unsigned>(strip_length)
        );
    }

    _handled_profile_present = true;
    _handled_profile_strip_length = strip_length;
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
        case DeviceMode::detached_background:
            return "detached_background";
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
