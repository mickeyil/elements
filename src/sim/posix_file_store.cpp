#include "sim/posix_file_store.h"

#include <fcntl.h>
#include <unistd.h>

#include <cerrno>
#include <climits>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <string>

namespace {

constexpr char STORAGE_ROOT_ENV[] = "ELEMENTS_SIM_STORAGE_ROOT";

bool is_safe_device_uid_(const char* s)
{
    if (s == nullptr || *s == '\0') return false;
    if (std::strcmp(s, ".") == 0 || std::strcmp(s, "..") == 0) return false;

    for (const char* p = s; *p != '\0'; ++p) {
        const char c = *p;
        if ((c >= 'A' && c <= 'Z') ||
            (c >= 'a' && c <= 'z') ||
            (c >= '0' && c <= '9') ||
            c == '-' || c == '_' || c == '.') {
            continue;
        }
        return false;
    }
    return true;
}

bool write_all_(int fd, const uint8_t* src, size_t len)
{
    size_t written = 0;
    while (written < len) {
        const size_t left = len - written;
        const size_t chunk =
            left > static_cast<size_t>(std::numeric_limits<ssize_t>::max())
                ? static_cast<size_t>(std::numeric_limits<ssize_t>::max())
                : left;
        const ssize_t n = ::write(fd, src + written, chunk);
        if (n > 0) {
            written += static_cast<size_t>(n);
            continue;
        }
        if (n < 0 && errno == EINTR) continue;
        return false;
    }
    return true;
}

std::filesystem::path file_path_(const std::filesystem::path& root,
                                 const char* name)
{
    return root / name;
}

std::filesystem::path temp_path_(const std::filesystem::path& root,
                                 const char* name)
{
    return root / ("." + std::string(name));
}

}  // namespace

PosixFileStore::PosixFileStore(const char* device_uid)
{
    if (!is_safe_device_uid_(device_uid)) {
        _state = FileStoreState::Faulted;
        return;
    }

    const char* base = std::getenv(STORAGE_ROOT_ENV);
    if (base == nullptr || *base == '\0') {
        base = ".";
    }
    _root = std::filesystem::path(base) / "simstorage" / device_uid;
}

bool PosixFileStore::ensure_ready_()
{
    if (_state == FileStoreState::Ready) return true;
    if (_state == FileStoreState::Faulted) return false;

    std::error_code ec;
    std::filesystem::create_directories(_root, ec);
    if (ec || !std::filesystem::is_directory(_root, ec) || ec) {
        _state = FileStoreState::Faulted;
        return false;
    }

    _state = FileStoreState::Ready;
    return true;
}

bool PosixFileStore::write(const char* name, const uint8_t* src, size_t len)
{
    if (len > 0 && src == nullptr) return false;
    if (!is_file_store_name(name)) return false;
    if (!ensure_ready_()) return false;

    const std::filesystem::path final = file_path_(_root, name);
    const std::filesystem::path tmp = temp_path_(_root, name);

    const std::string tmp_s = tmp.string();
    const int fd = ::open(tmp_s.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0666);
    if (fd < 0) return false;

    bool ok = write_all_(fd, src, len);
    if (ok && ::fsync(fd) < 0) ok = false;
    if (::close(fd) < 0) ok = false;

    if (!ok) {
        std::error_code ignored;
        std::filesystem::remove(tmp, ignored);
        return false;
    }

    std::error_code ec;
    std::filesystem::rename(tmp, final, ec);
    if (ec) {
        std::error_code ignored;
        std::filesystem::remove(tmp, ignored);
        return false;
    }
    return true;
}

int PosixFileStore::size(const char* name)
{
    if (!is_file_store_name(name)) return -1;
    if (!ensure_ready_()) return -1;

    std::error_code ec;
    const uintmax_t n = std::filesystem::file_size(file_path_(_root, name), ec);
    if (ec || n > static_cast<uintmax_t>(INT_MAX)) return -1;
    return static_cast<int>(n);
}

int PosixFileStore::read(const char* name, uint8_t* dst, size_t max_len)
{
    if (max_len > 0 && dst == nullptr) return -1;
    if (max_len > static_cast<size_t>(INT_MAX)) return -1;
    if (!is_file_store_name(name)) return -1;
    if (!ensure_ready_()) return -1;

    const std::string path_s = file_path_(_root, name).string();
    const int fd = ::open(path_s.c_str(), O_RDONLY);
    if (fd < 0) return -1;

    size_t total = 0;
    while (total < max_len) {
        const ssize_t n = ::read(fd, dst + total, max_len - total);
        if (n > 0) {
            total += static_cast<size_t>(n);
            continue;
        }
        if (n == 0) break;
        if (errno == EINTR) continue;
        ::close(fd);
        return -1;
    }

    if (::close(fd) < 0) return -1;
    return static_cast<int>(total);
}

bool PosixFileStore::remove(const char* name)
{
    if (!is_file_store_name(name)) return false;
    if (!ensure_ready_()) return false;

    std::error_code ec;
    const bool removed = std::filesystem::remove(file_path_(_root, name), ec);
    return removed && !ec;
}
