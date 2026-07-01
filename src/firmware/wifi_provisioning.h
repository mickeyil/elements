#pragma once

class WifiCredStore;

// Boot-time Wi-Fi provisioning captive portal for the ESP32 firmware.
//
// Activated from setup() before the App starts, when the provisioning
// jumper is installed or no credentials are stored. It brings up a
// WPA2 SoftAP named after the device UID, serves a one-page
// SSID/password form as a captive portal, and writes the submitted
// credential to the store.
//
// Firmware-only: it uses SoftAP and an HTTP server that the host
// simulator has no analog for; the sim never provisions.

// True if the provisioning jumper is installed (a wire from the jumper
// GPIO to an adjacent GND). Reads the pin once with an internal
// pull-up; boot-only, so call it from setup().
bool wifi_provisioning_jumper_present();

// Bring up the SoftAP and captive portal and serve it forever. Never
// returns: a successful save reboots the chip; anything else keeps the
// portal serving until the device is power-cycled.
[[noreturn]] void run_wifi_provisioning_portal(const char* ap_ssid,
                                               WifiCredStore& creds);
