#pragma once

#include <WiFi.h>
#include <WiFiUdp.h>

#include <memory>

#include "runtime_state.h"

class ESPDevice;

namespace firmware {

struct ControllerRuntimeContext {
    WiFiServer& tcp_server;
    WiFiClient& tcp_client;
    WiFiUDP& frame_udp;
    std::unique_ptr<ESPDevice>& device;
    TransportState& transport;
    WifiState& wifi;
    ControllerLinkState& link;
    DiagnosticsState& diag;
    RuntimeState& runtime;
};

void controller_clear_runtime_connection_state(ControllerRuntimeContext& ctx);
void controller_disconnect(ControllerRuntimeContext& ctx, const char* reason = nullptr);
void controller_accept(ControllerRuntimeContext& ctx);
void controller_handle_disconnect(ControllerRuntimeContext& ctx);
int controller_poll_tcp_commands(ControllerRuntimeContext& ctx);
void controller_send_frames(ControllerRuntimeContext& ctx);

}  // namespace firmware
