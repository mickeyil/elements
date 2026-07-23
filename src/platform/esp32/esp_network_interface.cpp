#include "platform/esp32/esp_network_interface.h"

#include "platform/esp32/wifi_manager.h"

EspNetworkInterface::EspNetworkInterface(WifiManager& wifi) : _wifi(wifi) {}

void EspNetworkInterface::begin()
{
    _wifi.begin();
}

NetworkTransition EspNetworkInterface::poll()
{
    return _wifi.poll();
}

bool EspNetworkInterface::is_up() const
{
    return _wifi.is_up();
}
