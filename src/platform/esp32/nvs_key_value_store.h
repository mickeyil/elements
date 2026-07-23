#pragma once

#include <Preferences.h>

#include "platform/key_value_store.h"

// NVS backing for KeyValueStore. The first operation opens the namespace
// and keeps it open for the lifetime of this object.

class NvsKeyValueStore : public KeyValueStore {
public:
    explicit NvsKeyValueStore(const char* name_space);

    KeyValueStoreState state() const override { return _state; }

    bool has_key(const char* key) override;
    bool remove(const char* key) override;

    bool get_u8(const char* key, uint8_t& out) override;
    bool get_u16(const char* key, uint16_t& out) override;
    bool get_f32(const char* key, float& out) override;

    bool put_u8(const char* key, uint8_t value) override;
    bool put_u16(const char* key, uint16_t value) override;
    bool put_f32(const char* key, float value) override;

    bool put_str(const char* key, const char* value) override;
    int  get_str(const char* key, char* out, size_t out_cap) override;

private:
    bool ensure_ready_();

    char _namespace[KEY_VALUE_STORE_MAX_NAME_SIZE + 1] = {};
    Preferences _preferences;
    KeyValueStoreState _state = KeyValueStoreState::NotReady;
};
