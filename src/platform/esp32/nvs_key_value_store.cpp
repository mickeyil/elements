#include "platform/esp32/nvs_key_value_store.h"

#include <cstdio>
#include <cstring>

NvsKeyValueStore::NvsKeyValueStore(const char* name_space)
{
    if (!is_key_value_store_name(name_space)) {
        _state = KeyValueStoreState::Faulted;
        return;
    }

    std::snprintf(_namespace, sizeof(_namespace), "%s", name_space);
}

bool NvsKeyValueStore::ensure_ready_()
{
    if (_state == KeyValueStoreState::Ready) return true;
    if (_state == KeyValueStoreState::Faulted) return false;

    if (!_preferences.begin(_namespace, false)) {
        _state = KeyValueStoreState::Faulted;
        return false;
    }

    _state = KeyValueStoreState::Ready;
    return true;
}

bool NvsKeyValueStore::has_key(const char* key)
{
    if (!is_key_value_store_name(key)) return false;
    if (!ensure_ready_()) return false;

    return _preferences.isKey(key);
}

bool NvsKeyValueStore::remove(const char* key)
{
    if (!is_key_value_store_name(key)) return false;
    if (!ensure_ready_()) return false;

    return _preferences.remove(key);
}

bool NvsKeyValueStore::get_u8(const char* key, uint8_t& out)
{
    if (!is_key_value_store_name(key)) return false;
    if (!ensure_ready_()) return false;
    if (_preferences.getType(key) != PT_U8) return false;

    out = _preferences.getUChar(key, 0);
    return true;
}

bool NvsKeyValueStore::get_u16(const char* key, uint16_t& out)
{
    if (!is_key_value_store_name(key)) return false;
    if (!ensure_ready_()) return false;
    if (_preferences.getType(key) != PT_U16) return false;

    out = _preferences.getUShort(key, 0);
    return true;
}

bool NvsKeyValueStore::get_f32(const char* key, float& out)
{
    if (!is_key_value_store_name(key)) return false;
    if (!ensure_ready_()) return false;
    if (_preferences.getBytesLength(key) != sizeof(out)) return false;

    return _preferences.getBytes(key, &out, sizeof(out)) == sizeof(out);
}

bool NvsKeyValueStore::put_u8(const char* key, uint8_t value)
{
    if (!is_key_value_store_name(key)) return false;
    if (!ensure_ready_()) return false;

    return _preferences.putUChar(key, value) == sizeof(value);
}

bool NvsKeyValueStore::put_u16(const char* key, uint16_t value)
{
    if (!is_key_value_store_name(key)) return false;
    if (!ensure_ready_()) return false;

    return _preferences.putUShort(key, value) == sizeof(value);
}

bool NvsKeyValueStore::put_f32(const char* key, float value)
{
    if (!is_key_value_store_name(key)) return false;
    if (!ensure_ready_()) return false;

    return _preferences.putBytes(key, &value, sizeof(value)) == sizeof(value);
}

bool NvsKeyValueStore::put_str(const char* key, const char* value)
{
    if (value == nullptr) return false;
    const size_t value_len = std::strlen(value);
    if (value_len > KEY_VALUE_STORE_MAX_VALUE_SIZE) return false;
    if (!is_key_value_store_name(key)) return false;
    if (!ensure_ready_()) return false;

    // putString returns strlen(value) on success and 0 on failure;
    // the empty-string success case needs isKey to disambiguate.
    const size_t written = _preferences.putString(key, value);
    if (value_len > 0) return written == value_len;
    return _preferences.isKey(key);
}

int NvsKeyValueStore::get_str(const char* key, char* out, size_t out_cap)
{
    if (out == nullptr || out_cap == 0) return -1;
    if (!is_key_value_store_name(key)) return -1;
    if (!ensure_ready_()) return -1;
    if (_preferences.getType(key) != PT_STR) return -1;

    // put_str enforces the cap, so tmp always fits. Zero-init is
    // load-bearing: getString returns 0 both for an empty string and
    // for a silent read failure, and leaves tmp untouched either way;
    // surfacing "" is correct for the first and graceful for the second.
    char tmp[KEY_VALUE_STORE_MAX_VALUE_SIZE + 1] = {};
    const size_t len = _preferences.getString(key, tmp, sizeof(tmp));

    if (len + 1 > out_cap) return -1;
    std::memcpy(out, tmp, len + 1);
    return static_cast<int>(len);
}
