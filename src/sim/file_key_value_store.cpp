#include "sim/file_key_value_store.h"

#include <fstream>
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

    std::ofstream out(tmp, std::ios::trunc);
    if (!out) return false;
    out << _data.dump(2) << '\n';
    out.close();
    if (!out) {
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
