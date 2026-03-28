#pragma once

#include <cstddef>
#include <cstdint>

namespace firmware {

constexpr uint16_t kTcpPort = 6053;
constexpr uint16_t kDiscoveryPort = 6040;
constexpr uint16_t kMaxDevicePixels = 250;

constexpr uint8_t kCmdConfigure = 0x04;
constexpr uint8_t kSyncReq = 0x01;
constexpr uint8_t kSyncResp = 0x02;
constexpr uint8_t kCmdSyncResult = 0x03;
constexpr uint8_t kCmdLoad = 0x10;
constexpr uint8_t kCmdStart = 0x11;
constexpr uint8_t kCmdJump = 0x12;
constexpr uint8_t kCmdPause = 0x13;
constexpr uint8_t kCmdResume = 0x14;
constexpr uint8_t kCmdStop = 0x15;
constexpr uint8_t kCmdReboot = 0x30;
constexpr uint8_t kCmdDebugSeek = 0x22;
constexpr uint8_t kCmdDebugStep = 0x23;
constexpr uint8_t kCmdAck = 0x80;

constexpr uint16_t kDiscoveryMagic = 0x454C;
constexpr uint8_t kDiscoveryTypeReject = 0x01;
constexpr uint8_t kDiscoveryReasonDuplicateUid = 0x01;

constexpr size_t kTcpBufInitial = 4096;
constexpr size_t kTcpMsgMax = 256 * 1024;

constexpr uint32_t kHelloIntervalMs = 500;
constexpr uint32_t kDuplicateHelloBackoffMs = 5000;
constexpr uint32_t kStatusIntervalMs = 5000;
constexpr uint32_t kWifiPreferredTimeoutMs = 4000;
constexpr uint32_t kWifiRetryIntervalMs = 5000;
constexpr uint32_t kWifiConnectTimeoutMs = 12000;
constexpr uint32_t kLoopDelayMs = 1;
constexpr uint32_t kRebootDelayMs = 100;

constexpr char kNvsNamespace[] = "elements";
constexpr char kNvsLastGoodSsidKey[] = "last_ssid";

}  // namespace firmware
