#pragma once

#include "platform/network_interface.h"

// NetInterface for the sim/host build: the LAN is always usable, so
// there is never a connection edge to report. The firmware counterpart is
// EspNetworkInterface over WifiManager.

class HostNetworkInterface : public NetInterface
{
public:
    void begin() override {}

    // Always up, so no transition ever happens; link_up/link_down are for
    // a real association edge that the host has nothing to report.
    NetworkTransition poll() override { return NetworkTransition::unchanged; }

    bool is_up() const override { return true; }
};
