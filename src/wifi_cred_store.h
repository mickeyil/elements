#pragma once

#include <cstddef>
#include <cstdint>

#include "key_value_store.h"

// Wi-Fi credential identity, sized for the buffers callers allocate.
// SSID: IEEE 802.11 caps at 32 bytes. Password: WPA2-PSK passphrase is
// 8-63 ASCII chars (0 for open networks). The +1 is the NUL terminator.
constexpr size_t WIFI_SSID_BUF_SIZE     = 33;
constexpr size_t WIFI_PASSWORD_BUF_SIZE = 64;

constexpr size_t WIFI_SSID_MAX_LEN     = WIFI_SSID_BUF_SIZE - 1;
constexpr size_t WIFI_PASSWORD_MAX_LEN = WIFI_PASSWORD_BUF_SIZE - 1;

// Largest number of credentials the store will hold. Drives the
// 'cred_<n>_*' key indexing; cred_31_ssid is the longest key name
// (12 chars), under the 15-char KeyValueStore limit.
constexpr size_t MAX_STORED_WIFI_CREDS = 32;

// Plain (ssid, password) pair. Used both by the compiled seed array in
// secrets.h and by WifiCredStore::seed_from. Pointers are caller-owned;
// the store copies the bytes during put().
struct WifiCredential {
    const char* ssid;
    const char* password;
};

// Wi-Fi credential book backed by a KeyValueStore. Stores up to
// MAX_STORED_WIFI_CREDS (ssid, password) pairs keyed by integer index
// inside the KV, plus a 'last_ssid' string pointing at the most recent
// successful association. The KV layout is an implementation detail;
// callers see credentials by SSID name.
//
// Caller-owned buffers, matching KeyValueStore::get_str: a caller hands
// in a char* + cap, the store fills it and NUL-terminates. Use the
// WIFI_*_BUF_SIZE constants to size stack buffers.
//
// Removal uses swap-with-last (the deleted slot is filled with the
// last cred, count is decremented). Order is not preserved; lookup is
// by name, so it does not matter.

class WifiCredStore {
public:
    explicit WifiCredStore(KeyValueStore& kv);

    // Add or overwrite a credential. Rejects ssid_len == 0,
    // ssid_len > WIFI_SSID_MAX_LEN, password_len > WIFI_PASSWORD_MAX_LEN,
    // or a full store (when adding a new SSID).
    bool put(const char* ssid, const char* password);

    // Read the password for ssid into password_out (cap bytes). Returns
    // false on unknown ssid, buffer too small, or storage error.
    bool get(const char* ssid, char* password_out, size_t cap) const;

    // Remove the credential with this SSID. Returns false if not found.
    bool remove(const char* ssid);

    bool contains(const char* ssid) const;

    size_t count() const;
    bool   empty() const { return count() == 0; }

    // Read the SSID at play-order index idx (in [0, count())). Caller
    // walks 0..count()-1 for a full scan.
    bool ssid_at(size_t idx, char* ssid_out, size_t cap) const;

    // Read / write the "last successful SSID" marker.
    bool last_ssid(char* out, size_t cap) const;
    bool set_last_ssid(const char* ssid);

    // Copy n entries from arr into the store via put(). Returns false on
    // the first put() that fails (rest are skipped). Idempotent in the
    // sense that overwriting an existing SSID is a no-cost write.
    bool seed_from(const WifiCredential* arr, size_t n);

private:
    // Linear search the indexed slots for ssid. Returns true and writes
    // the matching index to out_idx; false on not-found or read error.
    bool find_index_(const char* ssid, size_t& out_idx) const;

    bool   read_count_(uint8_t& out) const;
    bool   write_count_(uint8_t value);
    bool   read_ssid_at_(size_t idx, char* out, size_t cap) const;
    bool   read_password_at_(size_t idx, char* out, size_t cap) const;
    bool   write_slot_(size_t idx, const char* ssid, const char* password);
    bool   delete_slot_(size_t idx);

    KeyValueStore& _kv;
};
