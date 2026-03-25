#pragma once

#include <WiFi.h>

#include <cstdint>
#include <memory>

#include "runtime_state.h"

class ESPDevice;

namespace firmware {

struct TcpCommandContext {
    std::unique_ptr<ESPDevice>& device;
    TransportState& transport;
    ControllerLinkState& link;
    DiagnosticsState& diag;
    RuntimeState& runtime;
};

void handle_cmd_configure(
    TcpCommandContext& ctx,
    WiFiClient& client,
    const uint8_t* payload,
    uint32_t payload_len
);

void handle_cmd_sync_result(
    TcpCommandContext& ctx,
    const uint8_t* payload,
    uint32_t payload_len
);

void handle_cmd_load(
    TcpCommandContext& ctx,
    WiFiClient& client,
    const uint8_t* payload,
    uint32_t payload_len
);

void handle_cmd_start(
    TcpCommandContext& ctx,
    const uint8_t* payload,
    uint32_t payload_len
);

void handle_cmd_jump(
    TcpCommandContext& ctx,
    const uint8_t* payload,
    uint32_t payload_len
);

void handle_cmd_pause(TcpCommandContext& ctx);

void handle_cmd_resume(
    TcpCommandContext& ctx,
    const uint8_t* payload,
    uint32_t payload_len
);

void handle_cmd_stop(TcpCommandContext& ctx);

void handle_cmd_reboot(TcpCommandContext& ctx, WiFiClient& client);

}  // namespace firmware
