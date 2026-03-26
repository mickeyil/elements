#include "controller_runtime.h"

#include <IPAddress.h>

#include <cstring>

#include "diagnostics.h"
#include "esp_device.h"
#include "tcp_commands.h"
#include "wire_constants.h"

namespace firmware {

void controller_clear_runtime_connection_state(ControllerRuntimeContext& ctx)
{
    ctx.link.configured = false;
    ctx.transport.device_id = 0;
    ctx.transport.frame_port = 0;
    ctx.transport.controller_ip = IPAddress();
    ctx.link.tcp_buf_used = 0;
    ctx.link.last_sync_seq = 0;
    if (ctx.device) {
        ctx.device->clear_sync();
    }
}

void controller_disconnect(ControllerRuntimeContext& ctx, const char* reason)
{
    if (ctx.link.connected) {
        ctx.diag.tcp_disconnect_count += 1;
        if (reason && reason[0] != '\0') {
            log_line("[tcp] controller disconnected: %s", reason);
        } else {
            log_line("[tcp] controller disconnected");
        }
    }

    if (ctx.tcp_client) {
        ctx.tcp_client.stop();
    }
    ctx.link.connected = false;
    controller_clear_runtime_connection_state(ctx);
}

void controller_accept(ControllerRuntimeContext& ctx)
{
    if (!ctx.wifi.server_started) {
        return;
    }

    WiFiClient incoming = ctx.tcp_server.available();
    if (!incoming) {
        return;
    }

    if (ctx.tcp_client && ctx.tcp_client.connected()) {
        log_line(
            "[tcp] rejecting extra controller connection from %s",
            incoming.remoteIP().toString().c_str()
        );
        incoming.stop();
        return;
    }

    ctx.tcp_client = incoming;
    ctx.tcp_client.setNoDelay(true);
    controller_clear_runtime_connection_state(ctx);
    ctx.link.connected = true;
    ctx.diag.tcp_accept_count += 1;
    log_line("[tcp] controller connected: %s", ctx.tcp_client.remoteIP().toString().c_str());
}

void controller_handle_disconnect(ControllerRuntimeContext& ctx)
{
    if (!ctx.link.connected || ctx.tcp_client.connected()) {
        return;
    }
    controller_disconnect(ctx);
}

int controller_poll_tcp_commands(ControllerRuntimeContext& ctx)
{
    if (!(ctx.tcp_client && ctx.tcp_client.connected())) {
        return -1;
    }

    TcpCommandContext cmd_ctx{ctx.device, ctx.transport, ctx.link, ctx.diag, ctx.runtime};

    while (ctx.tcp_client.available() > 0) {
        if (ctx.link.tcp_buf.size() - ctx.link.tcp_buf_used < 512) {
            ctx.link.tcp_buf.resize(ctx.link.tcp_buf.size() * 2);
        }

        const int n = ctx.tcp_client.read(
            ctx.link.tcp_buf.data() + ctx.link.tcp_buf_used,
            ctx.link.tcp_buf.size() - ctx.link.tcp_buf_used
        );
        if (n < 0) {
            return -1;
        }
        if (n == 0) {
            break;
        }
        ctx.link.tcp_buf_used += static_cast<size_t>(n);
    }

    while (ctx.link.tcp_buf_used >= 4) {
        uint32_t msg_len = 0;
        memcpy(&msg_len, ctx.link.tcp_buf.data(), sizeof(msg_len));

        if (msg_len < 1) {
            ctx.link.tcp_buf_used -= 4;
            if (ctx.link.tcp_buf_used > 0) {
                memmove(ctx.link.tcp_buf.data(), ctx.link.tcp_buf.data() + 4, ctx.link.tcp_buf_used);
            }
            continue;
        }

        if (msg_len > kTcpMsgMax) {
            log_line("[tcp] message too large (%lu), dropping client", static_cast<unsigned long>(msg_len));
            return -1;
        }

        const size_t total = 4 + static_cast<size_t>(msg_len);
        if (ctx.link.tcp_buf_used < total) {
            if (ctx.link.tcp_buf.size() < total) {
                ctx.link.tcp_buf.resize(total);
            }
            break;
        }

        const uint8_t cmd_type = ctx.link.tcp_buf[4];
        const uint8_t* payload = ctx.link.tcp_buf.data() + 5;
        const uint32_t payload_len = msg_len - 1;

        switch (cmd_type) {
            case kCmdConfigure:
                handle_cmd_configure(cmd_ctx, ctx.tcp_client, payload, payload_len);
                break;

            case kCmdSyncResult:
                handle_cmd_sync_result(cmd_ctx, payload, payload_len);
                break;

            case kCmdLoad:
                handle_cmd_load(cmd_ctx, ctx.tcp_client, payload, payload_len);
                break;

            case kCmdStart:
                handle_cmd_start(cmd_ctx, payload, payload_len);
                break;

            case kCmdJump:
                handle_cmd_jump(cmd_ctx, payload, payload_len);
                break;

            case kCmdPause:
                handle_cmd_pause(cmd_ctx);
                break;

            case kCmdResume:
                handle_cmd_resume(cmd_ctx, payload, payload_len);
                break;

            case kCmdStop:
                handle_cmd_stop(cmd_ctx);
                break;

            case kCmdReboot:
                handle_cmd_reboot(cmd_ctx, ctx.tcp_client);
                break;

            case kCmdDebugSeek:
            case kCmdDebugStep:
                log_line("[tcp] ignored debug-only command 0x%02x", cmd_type);
                break;

            default:
                log_line("[tcp] ignored unknown command 0x%02x", cmd_type);
                break;
        }

        ctx.link.tcp_buf_used -= total;
        if (ctx.link.tcp_buf_used > 0) {
            memmove(ctx.link.tcp_buf.data(), ctx.link.tcp_buf.data() + total, ctx.link.tcp_buf_used);
        }
    }

    return 0;
}

void controller_send_frames(ControllerRuntimeContext& ctx)
{
    if (!ctx.link.configured || !ctx.device || ctx.transport.frame_port == 0) {
        return;
    }

    auto frames = ctx.device->drain_frames();
    for (auto& frame : frames) {
        uint8_t header[12];
        memcpy(header, &ctx.transport.device_id, 2);
        memcpy(header + 2, &frame.gen, 2);
        memcpy(header + 4, &frame.frame_index, 4);
        memcpy(header + 8, &frame.t_rel, 4);

        if (!ctx.frame_udp.beginPacket(ctx.transport.controller_ip, ctx.transport.frame_port)) {
            continue;
        }
        ctx.frame_udp.write(header, sizeof(header));
        if (!frame.rgb.empty()) {
            ctx.frame_udp.write(frame.rgb.data(), frame.rgb.size());
        }
        if (ctx.frame_udp.endPacket() != 0) {
            ctx.diag.frames_sent += 1;
            ctx.diag.have_frame_stats = true;
            ctx.diag.last_frame_gen = frame.gen;
            ctx.diag.last_frame_index = frame.frame_index;
            ctx.diag.last_frame_t_rel = frame.t_rel;
        }
    }
}

}  // namespace firmware
