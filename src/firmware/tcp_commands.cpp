#include "tcp_commands.h"

#include <Arduino.h>

#include <cstring>

#include "diagnostics.h"
#include "esp_device.h"
#include "wire_constants.h"

namespace firmware {
namespace {

bool send_all(WiFiClient& client, const uint8_t* data, size_t len)
{
    size_t offset = 0;
    while (offset < len) {
        const size_t written = client.write(data + offset, len - offset);
        if (written == 0) {
            return false;
        }
        offset += written;
    }
    return true;
}

void send_ack(WiFiClient& client, uint8_t status)
{
    uint8_t buf[6];
    const uint32_t len = 2;
    memcpy(buf, &len, sizeof(len));
    buf[4] = kCmdAck;
    buf[5] = status;
    send_all(client, buf, sizeof(buf));
}

}  // namespace

void handle_cmd_configure(
    TcpCommandContext& ctx,
    WiFiClient& client,
    const uint8_t* payload,
    uint32_t payload_len
)
{
    if (ctx.link.configured) {
        send_ack(client, 1);
        return;
    }
    if (payload_len < 6) {
        send_ack(client, 1);
        return;
    }

    uint16_t device_id = 0;
    uint16_t strip_length = 0;
    uint16_t frame_port = 0;
    memcpy(&device_id, payload, 2);
    memcpy(&strip_length, payload + 2, 2);
    memcpy(&frame_port, payload + 4, 2);

    if (strip_length < 1 || strip_length > kMaxDevicePixels || frame_port < 1) {
        send_ack(client, 1);
        return;
    }

    ctx.device.reset(new ESPDevice(strip_length));
    ctx.transport.device_id = device_id;
    ctx.transport.frame_port = frame_port;
    ctx.transport.controller_ip = client.remoteIP();
    ctx.link.configured = true;
    ctx.link.last_sync_seq = 0;
    ctx.diag.configure_count += 1;

    log_line(
        "[tcp] configure ok device_id=%u strip_length=%u frame_port=%u controller=%s",
        device_id,
        strip_length,
        frame_port,
        ctx.transport.controller_ip.toString().c_str()
    );
    send_ack(client, 0);
}

void handle_cmd_sync_result(
    TcpCommandContext& ctx,
    const uint8_t* payload,
    uint32_t payload_len
)
{
    if (!ctx.link.configured || !ctx.device || payload_len < 14) {
        return;
    }

    uint16_t seq = 0;
    uint32_t boot_token = 0;
    int64_t offset_us = 0;
    memcpy(&seq, payload, 2);
    memcpy(&boot_token, payload + 2, 4);
    memcpy(&offset_us, payload + 6, 8);
    if (boot_token != ctx.runtime.boot_token) {
        log_line(
            "[sync] stale result ignored seq=%u token=%lu current=%lu",
            static_cast<unsigned>(seq),
            static_cast<unsigned long>(boot_token),
            static_cast<unsigned long>(ctx.runtime.boot_token)
        );
        return;
    }
    if (seq < ctx.link.last_sync_seq) {
        log_line(
            "[sync] old result ignored seq=%u last=%u",
            static_cast<unsigned>(seq),
            static_cast<unsigned>(ctx.link.last_sync_seq)
        );
        return;
    }
    ctx.link.last_sync_seq = seq;
    ctx.device->handle_sync_result(offset_us);
    log_line(
        "[sync] result applied seq=%u offset_us=%lld",
        static_cast<unsigned>(seq),
        static_cast<long long>(offset_us)
    );
    log_line("[sync] ready for playback");
}

void handle_cmd_load(
    TcpCommandContext& ctx,
    WiFiClient& client,
    const uint8_t* payload,
    uint32_t payload_len
)
{
    if (!ctx.link.configured || !ctx.device) {
        send_ack(client, 2);
        return;
    }
    if (payload_len < 4) {
        send_ack(client, 1);
        return;
    }

    uint16_t device_id = 0;
    uint16_t gen = 0;
    memcpy(&device_id, payload, 2);
    memcpy(&gen, payload + 2, 2);
    ctx.transport.device_id = device_id;

    const uint8_t* blob = payload + 4;
    const size_t blob_len = payload_len - 4;
    const bool ok = ctx.device->handle_load(blob, blob_len, gen);
    ctx.diag.load_count += 1;
    log_line(
        "[tcp] load gen=%u bytes=%lu status=%s",
        gen,
        static_cast<unsigned long>(blob_len),
        ok ? "ok" : "decode-failed"
    );
    send_ack(client, ok ? 0 : 1);
}

void handle_cmd_start(
    TcpCommandContext& ctx,
    const uint8_t* payload,
    uint32_t payload_len
)
{
    if (ctx.link.configured && ctx.device && payload_len >= 8) {
        int64_t t0 = 0;
        memcpy(&t0, payload, 8);
        ctx.device->handle_start(t0);
        ctx.diag.start_count += 1;
        log_line("[tcp] start clock=%s", ctx.device->playback_uses_sync() ? "synced" : "local");
    }
}

void handle_cmd_jump(
    TcpCommandContext& ctx,
    const uint8_t* payload,
    uint32_t payload_len
)
{
    if (ctx.link.configured && ctx.device && payload_len >= 14) {
        int64_t t0 = 0;
        float t_rel = 0.0f;
        uint16_t gen = 0;
        memcpy(&t0, payload, 8);
        memcpy(&t_rel, payload + 8, 4);
        memcpy(&gen, payload + 12, 2);
        ctx.device->handle_jump(t0, t_rel, gen);
        ctx.diag.jump_count += 1;
        log_line(
            "[tcp] jump t_rel=%.3f gen=%u clock=%s",
            t_rel,
            gen,
            ctx.device->playback_uses_sync() ? "synced" : "local"
        );
    }
}

void handle_cmd_pause(TcpCommandContext& ctx)
{
    if (ctx.link.configured && ctx.device) {
        ctx.device->handle_pause();
        ctx.diag.pause_count += 1;
        log_line("[tcp] pause");
    }
}

void handle_cmd_resume(
    TcpCommandContext& ctx,
    const uint8_t* payload,
    uint32_t payload_len
)
{
    if (ctx.link.configured && ctx.device && payload_len >= 8) {
        int64_t t0 = 0;
        memcpy(&t0, payload, 8);
        ctx.device->handle_resume(t0);
        ctx.diag.resume_count += 1;
        log_line("[tcp] resume clock=%s", ctx.device->playback_uses_sync() ? "synced" : "local");
    }
}

void handle_cmd_stop(TcpCommandContext& ctx)
{
    if (ctx.link.configured && ctx.device) {
        ctx.device->handle_stop();
        ctx.diag.stop_count += 1;
        log_line("[tcp] stop");
    }
}

void handle_cmd_reboot(TcpCommandContext& ctx, WiFiClient& client)
{
    log_line("[tcp] reboot requested");
    send_ack(client, 0);
    ctx.runtime.reboot_pending = true;
    ctx.runtime.reboot_deadline_ms = millis() + kRebootDelayMs;
    log_line("[sys] reboot scheduled");
}

}  // namespace firmware
