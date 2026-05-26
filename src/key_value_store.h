#pragma once

#include <cstddef>
#include <cstdint>

// NVS keys and namespaces are capped at 15 chars.
constexpr size_t KEY_VALUE_STORE_MAX_NAME_SIZE = 15;

// Largest string value put_str / get_str will accept. Sized to cover the
// hottest current use, a Wi-Fi credential (32-byte SSID, 63-byte WPA2
// passphrase), plus margin.
constexpr size_t KEY_VALUE_STORE_MAX_VALUE_SIZE = 128;

inline bool is_key_value_store_name(const char* name)
{
    if (name == nullptr || *name == '\0') return false;

    const char first = *name;
    if (!((first >= 'A' && first <= 'Z') ||
          (first >= 'a' && first <= 'z') ||
          (first >= '0' && first <= '9'))) {
        return false;
    }

    size_t len = 0;
    for (const char* p = name; *p != '\0'; ++p) {
        const char c = *p;
        if (!((c >= 'A' && c <= 'Z') ||
              (c >= 'a' && c <= 'z') ||
              (c >= '0' && c <= '9') ||
              c == '_')) {
            return false;
        }
        ++len;
        if (len > KEY_VALUE_STORE_MAX_NAME_SIZE) return false;
    }
    return true;
}

enum class KeyValueStoreState : uint8_t {
    NotReady = 0,
    Ready,
    Faulted,
};

// A thin wrapper around small persistent settings. Each instance owns one
// namespace, so shared code never sees NVS handles or host file paths.
//
// Missing keys, type mismatches, and storage errors all return false.
// Call state() when the caller needs to distinguish a backend fault.

class KeyValueStore {
public:
    virtual ~KeyValueStore() = default;

    virtual KeyValueStoreState state() const = 0;

    virtual bool has_key(const char* key) = 0;
    virtual bool remove(const char* key) = 0;

    virtual bool get_u8(const char* key, uint8_t& out) = 0;
    virtual bool get_u16(const char* key, uint16_t& out) = 0;
    virtual bool get_f32(const char* key, float& out) = 0;

    virtual bool put_u8(const char* key, uint8_t value) = 0;
    virtual bool put_u16(const char* key, uint16_t value) = 0;
    virtual bool put_f32(const char* key, float value) = 0;

    // Store value as a NUL-terminated string. value_len excludes the NUL
    // and must be <= KEY_VALUE_STORE_MAX_VALUE_SIZE. Returns false on
    // length violation or storage error.
    virtual bool put_str(const char* key, const char* value) = 0;

    // Read the string at key into out (a buffer of out_cap bytes).
    // Writes a NUL terminator when out_cap > stored length. Returns the
    // string length (excluding NUL) on success, -1 on missing key, type
    // mismatch, or when out_cap is too small.
    virtual int get_str(const char* key, char* out, size_t out_cap) = 0;
};
