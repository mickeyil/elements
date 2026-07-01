#include "wifi_provisioning.h"

#include <Arduino.h>
#include <DNSServer.h>
#include <WebServer.h>
#include <WiFi.h>

#include "wifi_cred_store.h"

namespace {

// GPIO with an internal pull-up; a jumper to the adjacent GND pulls it
// LOW to force provisioning at boot. GPIO19 sits next to a GND on the
// DevKitC right rail, is not a strapping pin, and is otherwise unused.
constexpr uint8_t PROVISIONING_JUMPER_PIN = 19;

// WPA2 passphrase for the provisioning SoftAP. Hardcoded on purpose:
// it is join friction, not a secret. WPA2 needs 8 to 63 characters.
constexpr char AP_PASSPHRASE[] = "elemelem";

constexpr uint8_t  DNS_PORT  = 53;
constexpr uint16_t HTTP_PORT = 80;

// Grace on the "saved" page so the response flushes and the operator
// sees it before the chip resets into normal (station) mode.
constexpr uint32_t REBOOT_DELAY_MS = 2'500;

const char FORM_PAGE[] PROGMEM = R"HTML(<!DOCTYPE html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Wi-Fi setup</title></head><body>
<h1>Wi-Fi setup</h1>
<form method="POST" action="/save">
<label>SSID<br><input name="ssid" maxlength="32" required></label><br><br>
<label>Password<br><input name="password" type="password" maxlength="63"></label><br><br>
<button type="submit">Save</button>
</form></body></html>)HTML";

const char SAVED_PAGE[] PROGMEM = R"HTML(<!DOCTYPE html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Saved</title></head><body>
<h1>Saved</h1>
<p>Wi-Fi credentials stored. Remove the jumper; the device reboots and connects.</p>
</body></html>)HTML";

const char INVALID_PAGE[] PROGMEM = R"HTML(<!DOCTYPE html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Invalid</title></head><body>
<h1>Invalid SSID or password</h1>
<p><a href="/">Back</a></p>
</body></html>)HTML";

const char ERROR_PAGE[] PROGMEM = R"HTML(<!DOCTYPE html>
<html><head><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Error</title></head><body>
<h1>Save failed</h1>
<p>Storage error. <a href="/">Back</a></p>
</body></html>)HTML";

// Handle POST /save: validate against the store's limits, replace this
// SSID's entry (adding it if new), and mark it last-known-good. On
// success reply, then reboot into normal mode; on failure reply and
// keep serving so the operator can retry.
void handle_save_(WebServer& server, WifiCredStore& creds)
{
    const String ssid     = server.arg("ssid");
    const String password = server.arg("password");

    if (ssid.length() < 1 || ssid.length() > WIFI_SSID_MAX_LEN ||
        password.length() > WIFI_PASSWORD_MAX_LEN) {
        server.send_P(400, "text/html", INVALID_PAGE);
        return;
    }

    // One save operation: both writes must land. If put() succeeds but
    // set_last_ssid() fails the store is unhealthy, so report failure
    // rather than claim a partial save.
    if (!creds.put(ssid.c_str(), password.c_str()) ||
        !creds.set_last_ssid(ssid.c_str())) {
        server.send_P(500, "text/html", ERROR_PAGE);
        return;
    }

    server.send_P(200, "text/html", SAVED_PAGE);
    delay(REBOOT_DELAY_MS);
    ESP.restart();
}

}  // namespace

bool wifi_provisioning_jumper_present()
{
    pinMode(PROVISIONING_JUMPER_PIN, INPUT_PULLUP);
    delay(2);  // let the pull-up settle before sampling
    return digitalRead(PROVISIONING_JUMPER_PIN) == LOW;
}

void run_wifi_provisioning_portal(const char* ap_ssid, WifiCredStore& creds)
{
    WiFi.persistent(false);  // do not rewrite the ESP SDK Wi-Fi flash config
    WiFi.mode(WIFI_AP);
    if (!WiFi.softAP(ap_ssid, AP_PASSPHRASE)) {
        // No AP means the portal is unreachable; reboot and let the boot
        // provisioning check bring it up again rather than loop forever.
        delay(1'000);
        ESP.restart();
    }
    const IPAddress ip = WiFi.softAPIP();

    // Wildcard DNS so a joined phone's captive-portal probe resolves to
    // us and pops the login sheet. Non-fatal: if DNS does not start, the
    // form is still reachable at http://192.168.4.1/.
    DNSServer dns;
    dns.start(DNS_PORT, "*", ip);

    WebServer server(HTTP_PORT);

    server.on("/", HTTP_GET, [&server]() {
        server.send_P(200, "text/html", FORM_PAGE);
    });
    server.on("/save", HTTP_POST, [&server, &creds]() {
        handle_save_(server, creds);
    });
    // Send OS captive-check URLs (and anything else) back to the form.
    server.onNotFound([&server]() {
        server.sendHeader("Location", "/", true);
        server.send(302, "text/plain", "");
    });
    server.begin();

    for (;;) {
        dns.processNextRequest();
        server.handleClient();
        delay(1);  // yield to the Wi-Fi/idle tasks and pet the watchdog
    }
}
