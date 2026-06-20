#include "esp_file_store.h"

#include <FS.h>
#include <LittleFS.h>

#include <climits>
#include <cstdio>

namespace {

bool make_path_(const char* name, bool temp, char* out, size_t out_size)
{
    if (!is_file_store_name(name)) return false;

    const char* prefix = temp ? "/." : "/";
    const int n = std::snprintf(out, out_size, "%s%s", prefix, name);

    if (n <= 0) return false;                              // encoding error
    if (static_cast<size_t>(n) >= out_size) return false;  // truncated
    return true;
}

}  // namespace

bool EspFileStore::ensure_ready_()
{
    if (_state == FileStoreState::Ready) return true;
    if (_state == FileStoreState::Faulted) return false;

    // begin(true): format the partition if the mount fails.
    if (LittleFS.begin(true)) {
        _state = FileStoreState::Ready;
        return true;
    }

    _state = FileStoreState::Faulted;
    return false;
}

bool EspFileStore::write(const char* name, const uint8_t* src, size_t len)
{
    if (len > 0 && src == nullptr) return false;
    if (!ensure_ready_()) return false;

    char path[FILE_STORE_PATH_BUF_SIZE];
    char tmp[FILE_STORE_PATH_BUF_SIZE];
    if (!make_path_(name, false, path, sizeof(path)) ||
        !make_path_(name, true, tmp, sizeof(tmp))) {
        return false;
    }

    File file = LittleFS.open(tmp, FILE_WRITE);
    if (!file) return false;

    const size_t written = len == 0 ? 0 : file.write(src, len);
    file.flush();
    file.close();

    if (written != len) {
        LittleFS.remove(tmp);
        return false;
    }

    if (!LittleFS.rename(tmp, path)) {
        LittleFS.remove(tmp);
        return false;
    }
    return true;
}

int EspFileStore::size(const char* name)
{
    if (!ensure_ready_()) return -1;

    char path[FILE_STORE_PATH_BUF_SIZE];
    if (!make_path_(name, false, path, sizeof(path))) return -1;

    File file = LittleFS.open(path, FILE_READ);
    if (!file) return -1;

    const size_t n = file.size();
    file.close();
    if (n > static_cast<size_t>(INT_MAX)) return -1;
    return static_cast<int>(n);
}

int EspFileStore::read(const char* name, uint8_t* dst, size_t max_len)
{
    if (max_len > 0 && dst == nullptr) return -1;
    if (max_len > static_cast<size_t>(INT_MAX)) return -1;
    if (!ensure_ready_()) return -1;

    char path[FILE_STORE_PATH_BUF_SIZE];
    if (!make_path_(name, false, path, sizeof(path))) return -1;

    File file = LittleFS.open(path, FILE_READ);
    if (!file) return -1;

    size_t total = 0;
    while (total < max_len) {
        const int n = file.read(dst + total, max_len - total);
        if (n > 0) {
            total += static_cast<size_t>(n);
            continue;
        }
        if (n == 0) break;
        file.close();
        return -1;
    }

    file.close();
    return static_cast<int>(total);
}

bool EspFileStore::remove(const char* name)
{
    if (!ensure_ready_()) return false;

    char path[FILE_STORE_PATH_BUF_SIZE];
    if (!make_path_(name, false, path, sizeof(path))) return false;

    return LittleFS.remove(path);
}
