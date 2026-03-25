#include "diagnostics.h"

#include "wire_constants.h"

#include <Arduino.h>
#include <WiFi.h>

#include <cstdarg>
#include <cstdio>

namespace firmware {

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
    DiagnosticsState& diagnostics,
    const WifiState& wifi,
    const ControllerLinkState& link
)
{
    const uint32_t now = millis();
    if (
        diagnostics.last_status_ms != 0
        && now - diagnostics.last_status_ms < kStatusIntervalMs
    ) {
        return;
    }
    diagnostics.last_status_ms = now;

    const String ip = wifi.ready ? WiFi.localIP().toString() : String("-");
    const unsigned long uptime_s = now / 1000;
    if (diagnostics.have_frame_stats) {
        log_line(
            "[status] up=%lus wifi=%s ip=%s ctrl=%s cfg=%s tries=%lu ok=%lu hellos=%lu tcp=%lu/%lu frames=%llu last=%u/%lu/%.3f load=%lu start=%lu stop=%lu",
            uptime_s,
            wifi.ready ? "up" : "down",
            ip.c_str(),
            yes_no(link.connected),
            yes_no(link.configured),
            static_cast<unsigned long>(diagnostics.wifi_connect_attempts),
            static_cast<unsigned long>(diagnostics.wifi_connect_successes),
            static_cast<unsigned long>(diagnostics.hello_count),
            static_cast<unsigned long>(diagnostics.tcp_accept_count),
            static_cast<unsigned long>(diagnostics.tcp_disconnect_count),
            static_cast<unsigned long long>(diagnostics.frames_sent),
            static_cast<unsigned>(diagnostics.last_frame_gen),
            static_cast<unsigned long>(diagnostics.last_frame_index),
            static_cast<double>(diagnostics.last_frame_t_rel),
            static_cast<unsigned long>(diagnostics.load_count),
            static_cast<unsigned long>(diagnostics.start_count),
            static_cast<unsigned long>(diagnostics.stop_count)
        );
        return;
    }

    log_line(
        "[status] up=%lus wifi=%s ip=%s ctrl=%s cfg=%s tries=%lu ok=%lu hellos=%lu tcp=%lu/%lu frames=%llu load=%lu start=%lu stop=%lu",
        uptime_s,
        wifi.ready ? "up" : "down",
        ip.c_str(),
        yes_no(link.connected),
        yes_no(link.configured),
        static_cast<unsigned long>(diagnostics.wifi_connect_attempts),
        static_cast<unsigned long>(diagnostics.wifi_connect_successes),
        static_cast<unsigned long>(diagnostics.hello_count),
        static_cast<unsigned long>(diagnostics.tcp_accept_count),
        static_cast<unsigned long>(diagnostics.tcp_disconnect_count),
        static_cast<unsigned long long>(diagnostics.frames_sent),
        static_cast<unsigned long>(diagnostics.load_count),
        static_cast<unsigned long>(diagnostics.start_count),
        static_cast<unsigned long>(diagnostics.stop_count)
    );
}

}  // namespace firmware
