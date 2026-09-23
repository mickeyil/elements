#pragma once

#include "platform/network_interface.h"

class WifiManager;

// NetInterface adapter over WifiManager. Keeps the App's polling
// surface platform-agnostic.
//
// Also hosts the ArduinoOTA listener that takes firmware updates from
// the controller. It lives here because it must follow the Wi-Fi link
// (restart on link_up, service only while up) and poll() is the one
// ESP-specific call the App makes every tick; the App and the shared
// controller code stay unaware of it.

class EspNetworkInterface : public NetInterface {
public:
    explicit EspNetworkInterface(WifiManager& wifi);

    void              begin() override;
    NetworkTransition poll() override;
    bool              is_up() const override;

private:
    void start_ota_();

    WifiManager& _wifi;
    bool         _ota_listening = false;
};
