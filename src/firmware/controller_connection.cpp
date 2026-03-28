#include "controller_connection.h"

#include <Arduino.h>

#include <cstring>

#include "diagnostics.h"
#include "esp_device.h"
#include "hardware_profile.h"
#include "wire_constants.h"

namespace firmware {

ControllerConnection::ControllerConnection()
    : _tcp_server(kTcpPort),
      _tcp_buf(kTcpBufInitial)
{
}

void ControllerConnection::begin(ESPDevice& device, const DeviceIdentity& identity)
{
    _device = &device;
    _identity = &identity;
}

void ControllerConnection::start_if_needed()
{
    if (_state != ConnectionState::stopped) {
        return;
    }

    _tcp_server.begin();
    _tcp_server.setNoDelay(true);
    _state = ConnectionState::listening;
}

void ControllerConnection::flush_active_client()
{
    if (_tcp_client) {
        _tcp_client.flush();
    }
}

void ControllerConnection::stop(const char* reason)
{
    const bool had_client = has_active_client_();
    if (had_client) {
        _tcp_disconnect_count += 1;
        if (reason != nullptr && reason[0] != '\0') {
            log_line("[tcp] controller disconnected: %s", reason);
        } else {
            log_line("[tcp] controller disconnected");
        }
    }

    if (_tcp_client) {
        _tcp_client.stop();
    }
    _tcp_server.end();
    _frame_udp.stop();
    reset_session_state_();
    _state = ConnectionState::stopped;
}

ConnectionPollResult ControllerConnection::poll()
{
    ConnectionPollResult result;

    if (_state == ConnectionState::stopped) {
        return result;
    }

    if (has_active_client_() && !(_tcp_client && _tcp_client.connected())) {
        disconnect_client_();
        result.disconnected = true;
        return result;
    }

    accept_client_();

    if (!has_active_client_()) {
        return result;
    }
    if (!(_tcp_client && _tcp_client.connected())) {
        disconnect_client_();
        result.disconnected = true;
        return result;
    }

    if (poll_commands_(result) < 0) {
        disconnect_client_("socket error");
        result.disconnected = true;
    }
    return result;
}

void ControllerConnection::send_frames()
{
    if (!is_attached() || _frame_port == 0 || _device == nullptr) {
        return;
    }

    std::vector<EspRgbFrame> frames = _device->drain_frames();
    for (size_t i = 0; i < frames.size(); ++i) {
        EspRgbFrame& frame = frames[i];
        uint8_t header[12];
        memcpy(header, &_device_id, 2);
        memcpy(header + 2, &frame.gen, 2);
        memcpy(header + 4, &frame.frame_index, 4);
        memcpy(header + 8, &frame.t_rel, 4);

        if (!_frame_udp.beginPacket(_controller_ip, _frame_port)) {
            continue;
        }
        _frame_udp.write(header, sizeof(header));
        if (!frame.rgb.empty()) {
            _frame_udp.write(frame.rgb.data(), frame.rgb.size());
        }
        if (_frame_udp.endPacket() != 0) {
            _frames_sent += 1;
            _have_frame_stats = true;
            _last_frame_gen = frame.gen;
            _last_frame_index = frame.frame_index;
            _last_frame_t_rel = frame.t_rel;
        }
    }
}

bool ControllerConnection::is_attached() const
{
    return _state == ConnectionState::attached;
}

ConnectionSnapshot ControllerConnection::snapshot() const
{
    ConnectionSnapshot snapshot;
    snapshot.state = _state;
    snapshot.device_id = _device_id;
    snapshot.frame_port = _frame_port;
    snapshot.controller_ip = _controller_ip;
    snapshot.last_sync_seq = _last_sync_seq;
    snapshot.tcp_accept_count = _tcp_accept_count;
    snapshot.tcp_disconnect_count = _tcp_disconnect_count;
    snapshot.load_count = _load_count;
    snapshot.start_count = _start_count;
    snapshot.stop_count = _stop_count;
    snapshot.frames_sent = _frames_sent;
    snapshot.have_frame_stats = _have_frame_stats;
    snapshot.last_frame_gen = _last_frame_gen;
    snapshot.last_frame_index = _last_frame_index;
    snapshot.last_frame_t_rel = _last_frame_t_rel;
    return snapshot;
}

int ControllerConnection::poll_commands_(ConnectionPollResult& result)
{
    while (_tcp_client.available() > 0) {
        if (_tcp_buf.size() - _tcp_buf_used < 512) {
            _tcp_buf.resize(_tcp_buf.size() * 2);
        }

        const int n = _tcp_client.read(
            _tcp_buf.data() + _tcp_buf_used,
            _tcp_buf.size() - _tcp_buf_used
        );
        if (n < 0) {
            return -1;
        }
        if (n == 0) {
            break;
        }
        _tcp_buf_used += static_cast<size_t>(n);
    }

    while (_tcp_buf_used >= 4) {
        uint32_t msg_len = 0;
        memcpy(&msg_len, _tcp_buf.data(), sizeof(msg_len));

        if (msg_len < 1) {
            _tcp_buf_used -= 4;
            if (_tcp_buf_used > 0) {
                memmove(_tcp_buf.data(), _tcp_buf.data() + 4, _tcp_buf_used);
            }
            continue;
        }

        if (msg_len > kTcpMsgMax) {
            log_line("[tcp] message too large (%lu), dropping client", static_cast<unsigned long>(msg_len));
            return -1;
        }

        const size_t total = 4 + static_cast<size_t>(msg_len);
        if (_tcp_buf_used < total) {
            if (_tcp_buf.size() < total) {
                _tcp_buf.resize(total);
            }
            break;
        }

        const uint8_t cmd_type = _tcp_buf[4];
        const uint8_t* payload = _tcp_buf.data() + 5;
        const uint32_t payload_len = msg_len - 1;

        switch (cmd_type) {
            case kCmdSetProfile:
                handle_set_profile_(payload, payload_len);
                break;

            case kCmdAttach:
                handle_attach_(payload, payload_len);
                break;

            case kCmdSyncResult:
                handle_sync_result_(payload, payload_len);
                break;

            case kCmdLoad:
                handle_load_(payload, payload_len);
                break;

            case kCmdStart:
                handle_start_(payload, payload_len);
                break;

            case kCmdJump:
                handle_jump_(payload, payload_len);
                break;

            case kCmdPause:
                handle_pause_();
                break;

            case kCmdResume:
                handle_resume_(payload, payload_len);
                break;

            case kCmdStop:
                handle_stop_();
                break;

            case kCmdReboot:
                handle_reboot_(result);
                break;

            case kCmdDebugSeek:
            case kCmdDebugStep:
                log_line("[tcp] ignored debug-only command 0x%02x", cmd_type);
                break;

            default:
                log_line("[tcp] ignored unknown command 0x%02x", cmd_type);
                break;
        }

        _tcp_buf_used -= total;
        if (_tcp_buf_used > 0) {
            memmove(_tcp_buf.data(), _tcp_buf.data() + total, _tcp_buf_used);
        }
    }

    return 0;
}

void ControllerConnection::accept_client_()
{
    WiFiClient incoming = _tcp_server.available();
    if (!incoming) {
        return;
    }

    if (has_active_client_() && _tcp_client && _tcp_client.connected()) {
        log_line(
            "[tcp] rejecting extra controller connection from %s",
            incoming.remoteIP().toString().c_str()
        );
        incoming.stop();
        return;
    }

    _tcp_client = incoming;
    _tcp_client.setNoDelay(true);
    reset_session_state_();
    _state = ConnectionState::client_connected;
    _tcp_accept_count += 1;
    log_line("[tcp] controller connected: %s", _tcp_client.remoteIP().toString().c_str());
}

void ControllerConnection::disconnect_client_(const char* reason)
{
    if (has_active_client_()) {
        _tcp_disconnect_count += 1;
        if (reason != nullptr && reason[0] != '\0') {
            log_line("[tcp] controller disconnected: %s", reason);
        } else {
            log_line("[tcp] controller disconnected");
        }
    }

    if (_tcp_client) {
        _tcp_client.stop();
    }
    reset_session_state_();
    _state = ConnectionState::listening;
}

void ControllerConnection::reset_session_state_()
{
    _device_id = 0;
    _frame_port = 0;
    _controller_ip = IPAddress();
    _last_sync_seq = 0;
    _tcp_buf_used = 0;
}

bool ControllerConnection::has_active_client_() const
{
    return _state == ConnectionState::client_connected || _state == ConnectionState::attached;
}

bool ControllerConnection::send_all_(const uint8_t* data, size_t len)
{
    size_t offset = 0;
    while (offset < len) {
        const size_t written = _tcp_client.write(data + offset, len - offset);
        if (written == 0) {
            return false;
        }
        offset += written;
    }
    return true;
}

void ControllerConnection::send_ack_(uint8_t status)
{
    uint8_t buf[6];
    const uint32_t len = 2;
    memcpy(buf, &len, sizeof(len));
    buf[4] = kCmdAck;
    buf[5] = status;
    send_all_(buf, sizeof(buf));
}

uint8_t ControllerConnection::apply_profile_(const HardwareProfile& profile)
{
    if (_device == nullptr || !profile.is_valid()) {
        return 1;
    }

    const bool had_profile = _device->has_hardware_profile();
    const HardwareProfile current = _device->hardware_profile();
    const bool same_profile = had_profile && current == profile;
    const bool was_attached = is_attached();

    if (!_device->apply_hardware_profile(profile)) {
        return 1;
    }

    if (was_attached && !same_profile) {
        log_line(
            "[tcp] profile changed while attached; detaching device_id=%u old_len=%u new_len=%u",
            static_cast<unsigned>(_device_id),
            static_cast<unsigned>(current.strip_length),
            static_cast<unsigned>(profile.strip_length)
        );
        _device_id = 0;
        _frame_port = 0;
        _controller_ip = IPAddress();
        _last_sync_seq = 0;
        _state = ConnectionState::client_connected;
    } else {
        log_line(
            "[tcp] set_profile ok strip_length=%u%s",
            static_cast<unsigned>(profile.strip_length),
            same_profile && was_attached ? " (unchanged, still attached)" : ""
        );
    }

    return 0;
}

uint8_t ControllerConnection::attach_(uint16_t device_id, uint16_t frame_port)
{
    if (_device == nullptr || !_device->has_hardware_profile() || frame_port < 1) {
        return 1;
    }
    if (is_attached()) {
        return 1;
    }

    _device_id = device_id;
    _frame_port = frame_port;
    _controller_ip = _tcp_client.remoteIP();
    _last_sync_seq = 0;
    _state = ConnectionState::attached;

    log_line(
        "[tcp] attach ok device_id=%u frame_port=%u controller=%s",
        static_cast<unsigned>(device_id),
        static_cast<unsigned>(frame_port),
        _controller_ip.toString().c_str()
    );
    return 0;
}

void ControllerConnection::handle_set_profile_(const uint8_t* payload, uint32_t payload_len)
{
    if (payload_len < 2) {
        send_ack_(1);
        return;
    }

    uint16_t strip_length = 0;
    memcpy(&strip_length, payload, 2);
    const uint8_t status = apply_profile_(HardwareProfile(strip_length));
    send_ack_(status);
}

void ControllerConnection::handle_attach_(const uint8_t* payload, uint32_t payload_len)
{
    if (payload_len < 4) {
        send_ack_(1);
        return;
    }

    uint16_t device_id = 0;
    uint16_t frame_port = 0;
    memcpy(&device_id, payload, 2);
    memcpy(&frame_port, payload + 2, 2);
    const uint8_t status = attach_(device_id, frame_port);
    send_ack_(status);
}

void ControllerConnection::handle_sync_result_(const uint8_t* payload, uint32_t payload_len)
{
    if (!is_attached() || _device == nullptr || _identity == nullptr || payload_len < 14) {
        return;
    }

    uint16_t seq = 0;
    uint32_t boot_token = 0;
    int64_t offset_us = 0;
    memcpy(&seq, payload, 2);
    memcpy(&boot_token, payload + 2, 4);
    memcpy(&offset_us, payload + 6, 8);
    if (boot_token != _identity->boot_token) {
        log_line(
            "[sync] stale result ignored seq=%u token=%lu current=%lu",
            static_cast<unsigned>(seq),
            static_cast<unsigned long>(boot_token),
            static_cast<unsigned long>(_identity->boot_token)
        );
        return;
    }
    if (seq < _last_sync_seq) {
        log_line(
            "[sync] old result ignored seq=%u last=%u",
            static_cast<unsigned>(seq),
            static_cast<unsigned>(_last_sync_seq)
        );
        return;
    }
    _last_sync_seq = seq;
    _device->handle_sync_result(offset_us);
    log_line(
        "[sync] result applied seq=%u offset_us=%lld",
        static_cast<unsigned>(seq),
        static_cast<long long>(offset_us)
    );
    log_line("[sync] ready for playback");
}

void ControllerConnection::handle_load_(const uint8_t* payload, uint32_t payload_len)
{
    if (!is_attached() || _device == nullptr) {
        send_ack_(2);
        return;
    }
    if (payload_len < 2) {
        send_ack_(1);
        return;
    }

    uint16_t gen = 0;
    memcpy(&gen, payload, 2);

    const uint8_t* blob = payload + 2;
    const size_t blob_len = payload_len - 2;
    const bool ok = _device->handle_load(blob, blob_len, gen);
    _load_count += 1;
    log_line(
        "[tcp] load gen=%u bytes=%lu status=%s",
        gen,
        static_cast<unsigned long>(blob_len),
        ok ? "ok" : "decode-failed"
    );
    send_ack_(ok ? 0 : 1);
}

void ControllerConnection::handle_start_(const uint8_t* payload, uint32_t payload_len)
{
    if (is_attached() && _device != nullptr && payload_len >= 8) {
        int64_t t0 = 0;
        memcpy(&t0, payload, 8);
        _device->handle_start(t0);
        _start_count += 1;
        log_line("[tcp] start clock=%s", _device->playback_uses_sync() ? "synced" : "local");
    }
}

void ControllerConnection::handle_jump_(const uint8_t* payload, uint32_t payload_len)
{
    if (is_attached() && _device != nullptr && payload_len >= 14) {
        int64_t t0 = 0;
        float t_rel = 0.0f;
        uint16_t gen = 0;
        memcpy(&t0, payload, 8);
        memcpy(&t_rel, payload + 8, 4);
        memcpy(&gen, payload + 12, 2);
        _device->handle_jump(t0, t_rel, gen);
        log_line(
            "[tcp] jump t_rel=%.3f gen=%u clock=%s",
            t_rel,
            gen,
            _device->playback_uses_sync() ? "synced" : "local"
        );
    }
}

void ControllerConnection::handle_pause_()
{
    if (is_attached() && _device != nullptr) {
        _device->handle_pause();
        log_line("[tcp] pause");
    }
}

void ControllerConnection::handle_resume_(const uint8_t* payload, uint32_t payload_len)
{
    if (is_attached() && _device != nullptr && payload_len >= 8) {
        int64_t t0 = 0;
        memcpy(&t0, payload, 8);
        _device->handle_resume(t0);
        log_line("[tcp] resume clock=%s", _device->playback_uses_sync() ? "synced" : "local");
    }
}

void ControllerConnection::handle_stop_()
{
    if (is_attached() && _device != nullptr) {
        _device->handle_stop();
        _stop_count += 1;
        log_line("[tcp] stop");
    }
}

void ControllerConnection::handle_reboot_(ConnectionPollResult& result)
{
    log_line("[tcp] reboot requested");
    send_ack_(0);
    result.reboot_requested = true;
}

}  // namespace firmware
