#pragma once

#include <filesystem>

#include <nlohmann/json.hpp>

#include "key_value_store.h"

// JSON-file backing for KeyValueStore. Each sim device gets one JSON file
// per namespace under $ELEMENTS_SIM_STORAGE_ROOT/simstorage/<uid>/kvstore.

class FileKeyValueStore : public KeyValueStore {
public:
    FileKeyValueStore(const char* device_uid, const char* name_space);

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
    bool commit_();
    bool get_number_(const char* key, const char* type, nlohmann::json& value);
    bool put_number_(const char* key, const char* type, const nlohmann::json& value);

    std::filesystem::path _root;
    std::filesystem::path _path;
    nlohmann::json _data = nlohmann::json::object();
    KeyValueStoreState _state = KeyValueStoreState::NotReady;
};
