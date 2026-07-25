#pragma once

#include "platform/network_interface.h"

class WifiManager;

// NetInterface adapter over WifiManager. Keeps the App's polling
// surface platform-agnostic.

class EspNetworkInterface : public NetInterface {
public:
    explicit EspNetworkInterface(WifiManager& wifi);

    void              begin() override;
    NetworkTransition poll() override;
    bool              is_up() const override;

private:
    WifiManager& _wifi;
};
