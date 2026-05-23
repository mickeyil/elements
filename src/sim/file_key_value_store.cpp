#include "sim/file_key_value_store.h"

#include <fcntl.h>
#include <unistd.h>

#include <cerrno>
#include <fstream>
#include <limits>
#include <string>
#include <utility>

#include "sim/sim_storage.h"

namespace {

constexpr char KV_STORE_DIR[] = "kvstore";
constexpr char TYPE_KEY[] = "type";
constexpr char VALUE_KEY[] = "value";
constexpr char TYPE_U8[] = "u8";
constexpr char TYPE_U16[] = "u16";
constexpr char TYPE_F32[] = "f32";

std::filesystem::path temp_path_(const std::filesystem::path& path)
{
    return path.string() + ".tmp";
}

bool write_all_(int fd, const char* src, size_t len)
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

}  // namespace

FileKeyValueStore::FileKeyValueStore(const char* device_uid,
                                     const char* name_space)
{
    if (!is_key_value_store_name(name_space) ||
        !resolve_sim_store_root(device_uid, KV_STORE_DIR, _root)) {
        _state = KeyValueStoreState::Faulted;
        return;
    }

    _path = _root / (std::string(name_space) + ".json");
}

bool FileKeyValueStore::ensure_ready_()
{
    if (_state == KeyValueStoreState::Ready) return true;
    if (_state == KeyValueStoreState::Faulted) return false;

    std::error_code ec;
    std::filesystem::create_directories(_root, ec);
    if (ec || !std::filesystem::is_directory(_root, ec) || ec) {
        _state = KeyValueStoreState::Faulted;
        return false;
    }

    if (!std::filesystem::exists(_path, ec)) {
        if (ec) {
            _state = KeyValueStoreState::Faulted;
            return false;
        }
        _data = nlohmann::json::object();
        _state = KeyValueStoreState::Ready;
        return true;
    }

    std::ifstream in(_path);
    if (!in) {
        _state = KeyValueStoreState::Faulted;
        return false;
    }

    nlohmann::json parsed = nlohmann::json::parse(in, nullptr, false);
    if (parsed.is_discarded() || !parsed.is_object()) {
        _state = KeyValueStoreState::Faulted;
        return false;
    }

    _data = std::move(parsed);
    _state = KeyValueStoreState::Ready;
    return true;
}

bool FileKeyValueStore::commit_()
{
    const std::filesystem::path tmp = temp_path_(_path);
    const std::string text = _data.dump(2) + "\n";

    const std::string tmp_s = tmp.string();
    const int fd = ::open(tmp_s.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0666);
    if (fd < 0) return false;

    bool ok = write_all_(fd, text.data(), text.size());
    if (ok && ::fsync(fd) < 0) ok = false;
    if (::close(fd) < 0) ok = false;

    if (!ok) {
        std::error_code ignored;
        std::filesystem::remove(tmp, ignored);
        return false;
    }

    std::error_code ec;
    std::filesystem::rename(tmp, _path, ec);
    if (ec) {
        std::error_code ignored;
        std::filesystem::remove(tmp, ignored);
        return false;
    }
    return true;
}

bool FileKeyValueStore::has_key(const char* key)
{
    if (!is_key_value_store_name(key)) return false;
    if (!ensure_ready_()) return false;

    return _data.contains(key);
}

bool FileKeyValueStore::remove(const char* key)
{
    if (!is_key_value_store_name(key)) return false;
    if (!ensure_ready_()) return false;
    if (!_data.contains(key)) return false;

    nlohmann::json old = _data;
    _data.erase(key);
    if (!commit_()) {
        _data = std::move(old);
        return false;
    }
    return true;
}

bool FileKeyValueStore::get_number_(const char* key,
                                    const char* type,
                                    nlohmann::json& value)
{
    if (!is_key_value_store_name(key)) return false;
    if (!ensure_ready_()) return false;

    auto it = _data.find(key);
    if (it == _data.end() || !it->is_object()) return false;
    const nlohmann::json& entry = *it;
    if (!entry.contains(TYPE_KEY) || !entry[TYPE_KEY].is_string()) return false;
    if (entry[TYPE_KEY].get<std::string>() != type) return false;
    if (!entry.contains(VALUE_KEY)) return false;

    value = entry[VALUE_KEY];
    return true;
}

bool FileKeyValueStore::get_u8(const char* key, uint8_t& out)
{
    nlohmann::json value;
    if (!get_number_(key, TYPE_U8, value)) return false;
    if (!value.is_number_unsigned()) return false;

    const uint64_t n = value.get<uint64_t>();
    if (n > UINT8_MAX) return false;
    out = static_cast<uint8_t>(n);
    return true;
}

bool FileKeyValueStore::get_u16(const char* key, uint16_t& out)
{
    nlohmann::json value;
    if (!get_number_(key, TYPE_U16, value)) return false;
    if (!value.is_number_unsigned()) return false;

    const uint64_t n = value.get<uint64_t>();
    if (n > UINT16_MAX) return false;
    out = static_cast<uint16_t>(n);
    return true;
}

bool FileKeyValueStore::get_f32(const char* key, float& out)
{
    nlohmann::json value;
    if (!get_number_(key, TYPE_F32, value)) return false;
    if (!value.is_number()) return false;

    out = value.get<float>();
    return true;
}

bool FileKeyValueStore::put_number_(const char* key,
                                    const char* type,
                                    const nlohmann::json& value)
{
    if (!is_key_value_store_name(key)) return false;
    if (!ensure_ready_()) return false;

    nlohmann::json old = _data;
    _data[key] = {{TYPE_KEY, type}, {VALUE_KEY, value}};
    if (!commit_()) {
        _data = std::move(old);
        return false;
    }
    return true;
}

bool FileKeyValueStore::put_u8(const char* key, uint8_t value)
{
    return put_number_(key, TYPE_U8, static_cast<uint64_t>(value));
}

bool FileKeyValueStore::put_u16(const char* key, uint16_t value)
{
    return put_number_(key, TYPE_U16, static_cast<uint64_t>(value));
}

bool FileKeyValueStore::put_f32(const char* key, float value)
{
    return put_number_(key, TYPE_F32, value);
}
