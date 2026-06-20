#include <Arduino.h>
#include <FastLED.h>

#include "app.h"
#include "device_identity.h"
#include "discovery.h"
#include "esp_device_identity.h"
#include "esp_file_store.h"
#include "esp_frame_output.h"
#include "esp_network_interface.h"
#include "esp_system_platform.h"
#include "esp_tcp_transport.h"
#include "esp_udp_transport.h"
#include "hardware_profile_store.h"
#include "nvs_key_value_store.h"
#include "wifi_cred_store.h"
#include "wifi_manager.h"

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
EspTcpTransport   g_tcp;
EspFileStore      g_files;
NvsKeyValueStore  g_profile_kv(HARDWARE_PROFILE_KV_NAMESPACE);
EspSystemPlatform g_system;
EspFrameOutput    g_output;

// Filled in setup(); reading the MAC and drawing the boot token need
// a running chip, not static-init time. Everything holding a
// reference only dereferences it from begin()/tick().
DeviceIdentity g_identity;

DiscoveryClient g_discovery(g_discovery_udp, g_identity);
App g_app(g_network, g_discovery, g_tcp, g_sync_udp, g_files,
          g_profile_kv, g_system, g_output, g_identity);

}  // namespace

void setup()
{
    Serial.begin(115200);

    g_identity = make_esp_device_identity();

    FastLED.addLeds<WS2812B, LED_PIN, GRB>(g_output.leds(), MAX_STRIP_PIXELS);
    FastLED.setBrightness(255);

    g_app.begin();
    Serial.printf("elements device %s up\n", g_identity.uid);
}

void loop()
{
    g_app.tick();
}
