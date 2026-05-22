#include "firmware/nvs_key_value_store.h"

#include <cstdio>

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
