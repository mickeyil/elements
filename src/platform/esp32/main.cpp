#include <Arduino.h>
#include <FastLED.h>

#include "controller/app.h"
#include "platform/device_identity.h"
#include "controller/discovery.h"
#include "platform/esp32/esp_device_identity.h"
#include "platform/esp32/esp_file_store.h"
#include "platform/esp32/esp_frame_output.h"
#include "platform/esp32/esp_network_interface.h"
#include "platform/esp32/esp_system_platform.h"
#include "platform/esp32/esp_tcp_transport.h"
#include "platform/esp32/esp_udp_transport.h"
#include "controller/hardware_profile_store.h"
#include "platform/esp32/nvs_key_value_store.h"
#include "controller/slog.h"
#include "platform/wifi_cred_store.h"
#include "platform/esp32/wifi_manager.h"
#include "platform/esp32/wifi_provisioning.h"

// The firmware entry point: constructs the ESP platform pieces, hands
// them to the shared App, and ticks it from loop(). The sim
// counterpart is src/sim/main.cpp.

namespace {

constexpr uint8_t LED_PIN = 13;

constexpr char WIFI_KV_NAMESPACE[] = "wifi";

NvsKeyValueStore    g_wifi_kv(WIFI_KV_NAMESPACE);
WifiCredStore       g_wifi_creds(g_wifi_kv);
WifiManager         g_wifi(g_wifi_creds);
EspNetworkInterface g_network(g_wifi);

EspUdpTransport   g_discovery_udp;
EspUdpTransport   g_sync_udp;
EspUdpTransport   g_log_udp;
EspTcpTransport   g_tcp;
EspFileStore      g_files;
NvsKeyValueStore  g_profile_kv(HARDWARE_PROFILE_KV_NAMESPACE);
EspSystemPlatform g_system;
EspFrameOutput    g_output;

// Filled in setup(); reading the MAC and drawing the boot token need
// a running chip, not static-init time. References are dereferenced
// only after setup() has assigned this.
DeviceIdentity g_identity;

DiscoveryClient g_discovery(g_discovery_udp, g_identity);

// App construction touches the file store through AnimationStore. Keep
// it out of global initialization so an erased LittleFS partition is not
// formatted before the Arduino runtime is ready.
App* g_app = nullptr;

}  // namespace

void setup()
{
    Serial.begin(115200);

    g_identity = make_esp_device_identity();

    // Compiled-in credentials (secrets.h) land in the store first, so the
    // emptiness check below sees them and a fresh board with a known
    // network skips the portal.
    g_wifi.merge_dev_credentials();

    // Wi-Fi provisioning: an installed boot jumper or an empty credential
    // store diverts into the captive portal before the App starts. A
    // faulted NVS store also reads as empty here, so it lands in the
    // portal too; the save then fails visibly (put() returns false, error
    // page) instead of pretending to succeed. The portal reboots on a
    // successful save, so this call never returns when taken.
    if (wifi_provisioning_jumper_present() || g_wifi_creds.empty()) {
        run_wifi_provisioning_portal(g_identity.uid, g_wifi_creds);
    }

    static App app(g_network, g_discovery, g_tcp, g_sync_udp, g_log_udp,
                   g_files, g_profile_kv, g_system, g_output, g_identity);
    g_app = &app;

    FastLED.addLeds<WS2812B, LED_PIN, GRB>(g_output.leds(), MAX_STRIP_PIXELS);
    FastLED.setBrightness(255);

    g_app->begin();
    slog_info("elements device %s up", g_identity.uid);
}

void loop()
{
    if (g_app != nullptr) {
        g_app->tick();
    }
}
