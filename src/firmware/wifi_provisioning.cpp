#include "wifi_provisioning.h"

#include <Arduino.h>
#include <DNSServer.h>
#include <WebServer.h>
#include <WiFi.h>

#include <cstring>

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

// Networks shown in the dropdown, strongest first. Bounds the page size
// and the scan buffer.
constexpr size_t MAX_SCAN_ENTRIES = 12;

struct ScanEntry
{
    char    ssid[WIFI_SSID_BUF_SIZE] = {};
    int32_t rssi = 0;
};

struct ScanResults
{
    ScanEntry entries[MAX_SCAN_ENTRIES] = {};
    size_t    count = 0;
};

// Everything up to the card open; shared by the form and the message
// pages. Inputs are 17px so iOS does not zoom on focus.
const char PAGE_HEAD_CSS[] PROGMEM =
"<!DOCTYPE html><html><head>"
"<meta charset=\"utf-8\">"
"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
"<title>Wi-Fi setup</title><style>"
"*{box-sizing:border-box}"
"body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;"
"font-family:-apple-system,system-ui,sans-serif;"
"background:linear-gradient(160deg,#1b2a3a,#080a10);color:#e8eef5}"
".card{width:100%;max-width:360px;margin:16px;padding:26px;border-radius:20px;"
"background:#131b26;box-shadow:0 12px 34px rgba(0,0,0,.55)}"
"h1{margin:0 0 4px;font-size:26px}"
".sub{margin:0 0 18px;font-size:13px;color:#7f95ab;word-break:break-all}"
".msg{color:#b8c6d6;font-size:16px;line-height:1.5}"
"label{display:block;margin:16px 0 6px;font-size:14px;color:#b8c6d6}"
"select,input[type=text],input[type=password]{width:100%;font-size:17px;padding:13px;"
"border-radius:12px;border:1px solid #2a3a4d;background:#0d1520;color:#e8eef5}"
".show{display:flex;align-items:center;gap:8px;margin:14px 0 0;font-size:14px;color:#b8c6d6}"
".show input{width:18px;height:18px}"
"button{width:100%;margin-top:24px;font-size:18px;font-weight:600;padding:15px;"
"border:0;border-radius:12px;background:#2f81f7;color:#fff}"
"button:active{background:#2569cc}"
"a{color:#2f81f7}"
".rescan{display:block;text-align:center;margin-top:16px;font-size:14px;"
"text-decoration:none;color:#7f95ab}"
"</style></head><body><div class=\"card\">";

// Form body between the device subtitle and the SSID options.
const char FORM_BODY_MID[] PROGMEM =
"</div>"
"<form method=\"POST\" action=\"/save\">"
"<label>Network</label>"
"<select id=\"ssid\" name=\"ssid\" onchange=\"u()\">";

// Form body after the options: manual field, password, show toggle,
// button, rescan link, and the two small scripts.
const char FORM_BODY_TAIL[] PROGMEM =
"</select>"
"<input id=\"m\" name=\"m\" type=\"text\" placeholder=\"Network name\" maxlength=\"32\" "
"autocapitalize=\"off\" autocorrect=\"off\" spellcheck=\"false\" "
"style=\"display:none;margin-top:10px\">"
"<label>Password</label>"
"<input id=\"pw\" name=\"password\" type=\"password\" maxlength=\"63\">"
"<label class=\"show\"><input type=\"checkbox\" onclick=\"p(this)\">Show password</label>"
"<button type=\"submit\">Save</button>"
"</form>"
"<a class=\"rescan\" href=\"/rescan\">Rescan networks</a>"
"</div>"
"<script>"
"function u(){var s=document.getElementById('ssid'),m=document.getElementById('m'),"
"o=s.value=='';m.style.display=o?'block':'none';m.required=o;}"
"function p(c){document.getElementById('pw').type=c.checked?'text':'password';}"
"document.addEventListener('DOMContentLoaded',u);"
"</script></body></html>";

const char MSG_TAIL[] PROGMEM = "</div></body></html>";

// Escape untrusted text (scanned SSIDs, the device UID) for HTML text
// and double-quoted attribute contexts.
String html_escape_(const char* s)
{
    String out;
    for (const char* p = s; *p != '\0'; ++p) {
        switch (*p) {
            case '&':  out += "&amp;";  break;
            case '<':  out += "&lt;";   break;
            case '>':  out += "&gt;";   break;
            case '"':  out += "&quot;"; break;
            case '\'': out += "&#39;";  break;
            default:   out += *p;
        }
    }
    return out;
}

// Scan visible APs into out: dedupe by name keeping the strongest RSSI,
// keep the top MAX_SCAN_ENTRIES, then sort descending by RSSI. Blocking;
// the shared radio hops channels for a couple of seconds.
void run_scan_(ScanResults& out, const char* self_ssid)
{
    out.count = 0;

    const int found = WiFi.scanNetworks(false, false);
    for (int i = 0; i < found; ++i) {
        const String ssid = WiFi.SSID(i);
        if (ssid.length() == 0 || ssid.length() > WIFI_SSID_MAX_LEN) continue;
        if (ssid.equals(self_ssid)) continue;  // hide our own SoftAP
        const int32_t rssi = WiFi.RSSI(i);

        bool merged = false;
        for (size_t j = 0; j < out.count; ++j) {
            if (ssid.equals(out.entries[j].ssid)) {
                if (rssi > out.entries[j].rssi) out.entries[j].rssi = rssi;
                merged = true;
                break;
            }
        }
        if (merged) continue;

        size_t slot = out.count;
        if (out.count < MAX_SCAN_ENTRIES) {
            ++out.count;
        } else {
            // Full: replace the weakest, but only if this one beats it.
            size_t weakest = 0;
            for (size_t j = 1; j < out.count; ++j) {
                if (out.entries[j].rssi < out.entries[weakest].rssi) weakest = j;
            }
            if (rssi <= out.entries[weakest].rssi) continue;
            slot = weakest;
        }
        std::snprintf(out.entries[slot].ssid, WIFI_SSID_BUF_SIZE, "%s", ssid.c_str());
        out.entries[slot].rssi = rssi;
    }
    if (found >= 0) WiFi.scanDelete();

    // Insertion sort by RSSI descending: strongest network appears first.
    for (size_t i = 1; i < out.count; ++i) {
        const ScanEntry current = out.entries[i];
        size_t j = i;
        while (j > 0 && current.rssi > out.entries[j - 1].rssi) {
            out.entries[j] = out.entries[j - 1];
            --j;
        }
        out.entries[j] = current;
    }
}

// Build the provisioning form: styled shell, device UID subtitle, the
// scanned networks as options (escaped), and a trailing "Other" entry.
String build_form_page_(const char* ap_ssid, const ScanResults& scan)
{
    String page;
    page.reserve(2400);
    page += FPSTR(PAGE_HEAD_CSS);
    page += "<h1>Wi-Fi setup</h1><div class=\"sub\">";
    page += html_escape_(ap_ssid);
    page += FPSTR(FORM_BODY_MID);

    for (size_t i = 0; i < scan.count; ++i) {
        const String esc = html_escape_(scan.entries[i].ssid);
        page += "<option value=\"";
        page += esc;
        page += "\">";
        page += esc;
        page += "</option>";
    }

    // Empty value marks manual entry; scanned SSIDs are never empty.
    page += "<option value=\"\"";
    if (scan.count == 0) page += " selected";  // no networks: manual entry only
    page += ">Other network...</option>";

    page += FPSTR(FORM_BODY_TAIL);
    return page;
}

// A styled result page: heading plus one paragraph of trusted markup.
void send_message_(WebServer& server, int code, const char* heading, const char* body_html)
{
    String page;
    page.reserve(900);
    page += FPSTR(PAGE_HEAD_CSS);
    page += "<h1>";
    page += heading;
    page += "</h1><p class=\"msg\">";
    page += body_html;
    page += "</p>";
    page += FPSTR(MSG_TAIL);
    server.send(code, "text/html", page);
}

// Handle POST /save: resolve the SSID (dropdown pick or manual "Other"
// entry), validate against the store's limits, replace this SSID's entry
// (adding it if new), and mark it last-known-good. On success reply, then
// reboot into normal mode; on failure reply and keep serving for a retry.
void handle_save_(WebServer& server, WifiCredStore& creds)
{
    // Empty selection is the "Other network..." option; fall back to the
    // manual field.
    String ssid = server.arg("ssid");
    if (ssid.length() == 0) {
        ssid = server.arg("m");
    }
    const String password = server.arg("password");

    if (ssid.length() < 1 || ssid.length() > WIFI_SSID_MAX_LEN ||
        password.length() > WIFI_PASSWORD_MAX_LEN) {
        send_message_(server, 400, "Invalid entry",
                      "SSID or password out of range. <a href=\"/\">Back</a>");
        return;
    }

    // One save operation: both writes must land. If put() succeeds but
    // set_last_ssid() fails the store is unhealthy, so report failure
    // rather than claim a partial save.
    if (!creds.put(ssid.c_str(), password.c_str()) ||
        !creds.set_last_ssid(ssid.c_str())) {
        send_message_(server, 500, "Save failed",
                      "Storage error. <a href=\"/\">Back</a>");
        return;
    }

    send_message_(server, 200, "Saved",
                  "Remove the jumper; the device reboots and connects.");
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
    // AP_STA so the portal can scan for networks while the AP stays up.
    WiFi.mode(WIFI_AP_STA);
    if (!WiFi.softAP(ap_ssid, AP_PASSPHRASE)) {
        // No AP means the portal is unreachable; reboot and let the boot
        // provisioning check bring it up again rather than loop forever.
        delay(1'000);
        ESP.restart();
    }
    const IPAddress ip = WiFi.softAPIP();

    // Scan once before any client joins, then serve the cached list.
    ScanResults scan;
    run_scan_(scan, ap_ssid);

    // Wildcard DNS so a joined phone's captive-portal probe resolves to
    // us and pops the login sheet. Non-fatal: if DNS does not start, the
    // form is still reachable at http://192.168.4.1/.
    DNSServer dns;
    dns.start(DNS_PORT, "*", ip);

    WebServer server(HTTP_PORT);

    server.on("/", HTTP_GET, [&server, ap_ssid, &scan]() {
        server.send(200, "text/html", build_form_page_(ap_ssid, scan));
    });
    server.on("/rescan", HTTP_GET, [&server, &scan, ap_ssid]() {
        run_scan_(scan, ap_ssid);
        server.sendHeader("Location", "/", true);
        server.send(302, "text/plain", "");
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
