#include "diagnostics.h"

#include <Arduino.h>

#include <cstdarg>
#include <cstdio>

#include "link_protocol.h"

namespace {

bool connection_has_client_(ConnectionState state)
{
    return state == ConnectionState::client_connected || state == ConnectionState::attached;
}

}  // namespace

const char* yes_no(bool value)
{
    return value ? "yes" : "no";
}

void log_line(const char* fmt, ...)
{
    char message[256];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(message, sizeof(message), fmt, ap);
    va_end(ap);
    Serial.println(message);
}

void maybe_log_status(
    uint32_t& last_status_ms,
    const char* mode,
    const WifiSnapshot& wifi,
    const DiscoverySnapshot& discovery,
    const ConnectionSnapshot& connection
)
{
    const uint32_t now = millis();
    if (last_status_ms != 0 && now - last_status_ms < kStatusIntervalMs) {
        return;
    }
    last_status_ms = now;

    const String ip = wifi.ready ? wifi.local_ip.toString() : String("-");
    const unsigned long uptime_s = now / 1000;
    log_line(
        "[status] up=%lus mode=%s wifi=%s ip=%s ctrl=%s att=%s tries=%lu ok=%lu hellos=%lu tcp=%lu/%lu load=%lu start=%lu stop=%lu",
        uptime_s,
        mode,
        wifi.ready ? "up" : "down",
        ip.c_str(),
        yes_no(connection_has_client_(connection.state)),
        yes_no(connection.state == ConnectionState::attached),
        static_cast<unsigned long>(wifi.connect_attempts),
        static_cast<unsigned long>(wifi.connect_successes),
        static_cast<unsigned long>(discovery.hello_count),
        static_cast<unsigned long>(connection.tcp_accept_count),
        static_cast<unsigned long>(connection.tcp_disconnect_count),
        static_cast<unsigned long>(connection.load_count),
        static_cast<unsigned long>(connection.start_count),
        static_cast<unsigned long>(connection.stop_count)
    );
}
