#pragma once

#include <chrono>
#include <cstdarg>
#include <cstdio>
#include <fstream>
#include <iomanip>
#include <mutex>
#include <sstream>
#include <string>
#include <sys/stat.h>

namespace slog {

namespace detail {
    inline std::mutex& mutex() { static std::mutex m; return m; }
    inline std::ofstream& file() { static std::ofstream f; return f; }
}  // namespace detail

inline std::string timestamp_now()
{
    using namespace std::chrono;
    const auto now = system_clock::now();
    const auto millis = duration_cast<milliseconds>(now.time_since_epoch()) % 1000;
    const auto tt = system_clock::to_time_t(now);

    std::tm tm{};
    localtime_r(&tt, &tm);

    std::ostringstream out;
    out << std::put_time(&tm, "%Y-%m-%d %H:%M:%S.")
        << std::setw(3) << std::setfill('0') << millis.count();
    return out.str();
}

inline void init(const std::string& log_file = {})
{
    if (log_file.empty()) {
        return;
    }

    std::lock_guard<std::mutex> lock(detail::mutex());
    if (detail::file().is_open()) {
        return;
    }

    auto pos = log_file.rfind('/');
    if (pos != std::string::npos) {
        std::string dir = log_file.substr(0, pos);
        for (size_t i = 1; i <= dir.size(); ++i) {
            if (i == dir.size() || dir[i] == '/') {
                mkdir(dir.substr(0, i).c_str(), 0755);
            }
        }
    }

    detail::file().open(log_file, std::ios::app);
    if (!detail::file().is_open()) {
        std::fprintf(stderr, "slogger: failed to open log file: %s\n", log_file.c_str());
    }
}

inline void vlog(char level, const char* fmt, va_list ap)
{
    char message[1024];
    std::vsnprintf(message, sizeof(message), fmt, ap);

    const std::string line =
        "[" + timestamp_now() + "] -" + std::string(1, level) + "- : " + message;

    std::lock_guard<std::mutex> lock(detail::mutex());
    std::fprintf(stdout, "%s\n", line.c_str());
    std::fflush(stdout);
    if (detail::file().is_open()) {
        detail::file() << line << '\n';
        detail::file().flush();
    }
}

inline void info(const char* fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vlog('I', fmt, ap);
    va_end(ap);
}

inline void warn(const char* fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vlog('W', fmt, ap);
    va_end(ap);
}

inline void error(const char* fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vlog('E', fmt, ap);
    va_end(ap);
}

} // namespace slog
