#include "platform/esp32/esp_network_interface.h"

#include <ArduinoOTA.h>

#include "controller/slog.h"
#include "platform/esp32/wifi_manager.h"

EspNetworkInterface::EspNetworkInterface(WifiManager& wifi) : _wifi(wifi) {}

void EspNetworkInterface::begin()
{
    _wifi.begin();

    // The controller aims the update at the address a device's DISCOVER
    // came from, so mDNS advertisement is not needed.
    ArduinoOTA.setMdnsEnabled(false);
#ifdef ELEMENTS_OTA_PASSWORD
    // Must match controller.ota_password on the controller.
    ArduinoOTA.setPassword(ELEMENTS_OTA_PASSWORD);
#endif
    // Callbacks run inside ArduinoOTA.handle(), i.e. on the main loop,
    // which is what slog requires. The transfer itself blocks that loop
    // until the image is in, and on success ArduinoOTA restarts the chip
    // right after onEnd, so nothing here needs tearing down.
    ArduinoOTA.onStart([] { slog_info("ota: update started"); });
    ArduinoOTA.onEnd([] { slog_info("ota: update complete, rebooting"); });
    ArduinoOTA.onError([](ota_error_t error) {
        slog_error("ota: update failed (ota_error_t %u)", static_cast<unsigned>(error));
    });
}

NetworkTransition EspNetworkInterface::poll()
{
    const NetworkTransition transition = _wifi.poll();
    if (transition == NetworkTransition::link_up) {
        start_ota_();
    }
    if (_ota_listening && _wifi.is_up()) {
        ArduinoOTA.handle();
    }
    return transition;
}

bool EspNetworkInterface::is_up() const
{
    return _wifi.is_up();
}

void EspNetworkInterface::start_ota_()
{
    // A reconnect may bring a new address; rebind the UDP listener
    // rather than trust a socket opened on the previous link.
    if (_ota_listening) {
        ArduinoOTA.end();
    }
    ArduinoOTA.begin();
    _ota_listening = true;
    slog_info("ota: listening on udp 3232");
}
