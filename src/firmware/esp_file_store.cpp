#include "esp_file_store.h"

#include <FS.h>
#include <LittleFS.h>

#include <climits>
#include <cstdio>
#include <cstring>

namespace {

bool is_flat_name_(const char* name)
{
    if (name == nullptr || *name == '\0') return false;
    if (std::strcmp(name, ".") == 0 || std::strcmp(name, "..") == 0) return false;

    size_t len = 0;
    for (const char* p = name; *p != '\0'; ++p) {
        if (*p == '/' || *p == '\\') return false;
        ++len;
        if (len > FILE_STORE_MAX_NAME_SIZE) return false;
    }
    return true;
}

bool make_path_(const char* name, bool temp, char* out, size_t out_size)
{
    if (!is_flat_name_(name)) return false;

    const int n = temp
        ? std::snprintf(out, out_size, "/%s%s", name, FILE_STORE_TMP_SUFFIX)
        : std::snprintf(out, out_size, "/%s", name);
    return n > 0 && static_cast<size_t>(n) < out_size;
}

}  // namespace

bool EspFileStore::ensure_ready_()
{
    if (_state == FileStoreState::Ready) return true;
    if (_state == FileStoreState::Faulted) return false;

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

    const int n = max_len == 0 ? 0 : file.read(dst, max_len);
    file.close();
    return n < 0 ? -1 : n;
}

bool EspFileStore::remove(const char* name)
{
    if (!ensure_ready_()) return false;

    char path[FILE_STORE_PATH_BUF_SIZE];
    if (!make_path_(name, false, path, sizeof(path))) return false;

    return LittleFS.remove(path);
}
