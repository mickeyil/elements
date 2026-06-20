#pragma once

#include "network_interface.h"

class WifiManager;

// NetworkInterface adapter over WifiManager. Keeps the App's polling
// surface platform-agnostic.

class EspNetworkInterface : public NetworkInterface {
public:
    explicit EspNetworkInterface(WifiManager& wifi);

    void              begin() override;
    NetworkTransition poll() override;
    bool              is_up() const override;

private:
    WifiManager& _wifi;
};
