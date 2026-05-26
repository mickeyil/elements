#pragma once

#include "network_interface.h"

class WifiManager;

// NetworkInterface impl for ESP firmware: a thin shim around
// WifiManager, which holds all the Wi-Fi state and the credential walk.
// Lives separately so the shared App code sees only the abstract
// NetworkInterface seam.

class EspNetworkInterface : public NetworkInterface {
public:
    explicit EspNetworkInterface(WifiManager& wifi);

    void              begin() override;
    NetworkTransition poll() override;
    bool              is_up() const override;

private:
    WifiManager& _wifi;
};
