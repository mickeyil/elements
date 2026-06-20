#pragma once

#include <cstddef>
#include <cstdint>

#include "key_value_store.h"

// SSID: 32 bytes + NUL. Password (WPA2-PSK): 63 bytes + NUL; 0 for open.
constexpr size_t WIFI_SSID_BUF_SIZE     = 33;
constexpr size_t WIFI_PASSWORD_BUF_SIZE = 64;

constexpr size_t WIFI_SSID_MAX_LEN     = WIFI_SSID_BUF_SIZE - 1;
constexpr size_t WIFI_PASSWORD_MAX_LEN = WIFI_PASSWORD_BUF_SIZE - 1;

// Keep the saved Wi-Fi list small; search falls back to all entries.
constexpr size_t MAX_STORED_WIFI_CREDS = 8;

// (ssid, password) pair. Pointers are borrowed; put() copies the bytes.
struct WifiCredential {
    const char* ssid;
    const char* password;
};

// Credential book backed by a KeyValueStore: name-keyed put/get/remove,
// plus a last-known-good marker. Removal is swap-with-last, so
// iteration order is not stable; lookup is by SSID name.
//
// Caller-owned char* buffers (matches KeyValueStore::get_str). Use the
// WIFI_*_BUF_SIZE constants for stack buffers.

class WifiCredStore {
public:
    explicit WifiCredStore(KeyValueStore& kv);

    // Add or overwrite. False on bad sizes or full store.
    bool put(const char* ssid, const char* password);

    // Copy this SSID's password into password_out (cap bytes).
    bool get(const char* ssid, char* password_out, size_t cap) const;

    bool remove(const char* ssid);
    bool contains(const char* ssid) const;

    size_t count() const;
    bool   empty() const { return count() == 0; }

    // SSID at iteration index. idx must be in [0, count()).
    bool ssid_at(size_t idx, char* ssid_out, size_t cap) const;

    bool last_ssid(char* out, size_t cap) const;
    bool set_last_ssid(const char* ssid);

    // Reconcile against a compiled list. Adds new SSIDs, overwrites
    // changed passwords, skips identical entries (no NVS write), and
    // leaves credentials not in arr untouched. Compiled list wins on
    // same-SSID password conflict. Stops at the first failure.
    bool merge_from(const WifiCredential* arr, size_t n);

private:
    bool find_index_(const char* ssid, size_t& out_idx) const;

    bool   read_count_(uint8_t& out) const;
    bool   write_count_(uint8_t value);
    bool   read_ssid_at_(size_t idx, char* out, size_t cap) const;
    bool   read_password_at_(size_t idx, char* out, size_t cap) const;
    bool   write_slot_(size_t idx, const char* ssid, const char* password);
    bool   delete_slot_(size_t idx);

    KeyValueStore& _kv;
};
