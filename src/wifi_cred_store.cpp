#include "wifi_cred_store.h"

#include <cstdio>
#include <cstring>

namespace {

constexpr char COUNT_KEY[]   = "cred_count";
constexpr char LAST_SSID_KEY[] = "last_ssid";

void make_ssid_key_(size_t idx, char* out, size_t out_cap)
{
    std::snprintf(out, out_cap, "cred_%u_ssid", static_cast<unsigned>(idx));
}

void make_pwd_key_(size_t idx, char* out, size_t out_cap)
{
    std::snprintf(out, out_cap, "cred_%u_pwd", static_cast<unsigned>(idx));
}

bool valid_ssid_(const char* ssid)
{
    if (ssid == nullptr) return false;
    const size_t len = std::strlen(ssid);
    return len > 0 && len <= WIFI_SSID_MAX_LEN;
}

bool valid_password_(const char* password)
{
    if (password == nullptr) return false;
    return std::strlen(password) <= WIFI_PASSWORD_MAX_LEN;
}

}  // namespace

WifiCredStore::WifiCredStore(KeyValueStore& kv) : _kv(kv) {}

bool WifiCredStore::put(const char* ssid, const char* password)
{
    if (!valid_ssid_(ssid) || !valid_password_(password)) return false;

    size_t existing = 0;
    if (find_index_(ssid, existing)) {
        return write_slot_(existing, ssid, password);
    }

    uint8_t count = 0;
    read_count_(count);
    if (count >= MAX_STORED_WIFI_CREDS) return false;

    if (!write_slot_(count, ssid, password)) return false;
    return write_count_(static_cast<uint8_t>(count + 1));
}

bool WifiCredStore::get(const char* ssid, char* password_out, size_t cap) const
{
    if (password_out == nullptr || cap == 0) return false;
    if (!valid_ssid_(ssid)) return false;

    size_t idx = 0;
    if (!find_index_(ssid, idx)) return false;
    return read_password_at_(idx, password_out, cap);
}

bool WifiCredStore::remove(const char* ssid)
{
    if (!valid_ssid_(ssid)) return false;

    size_t idx = 0;
    if (!find_index_(ssid, idx)) return false;

    uint8_t count = 0;
    if (!read_count_(count) || count == 0) return false;

    const size_t last = static_cast<size_t>(count - 1);
    if (idx != last) {
        char tail_ssid[WIFI_SSID_BUF_SIZE];
        char tail_pwd[WIFI_PASSWORD_BUF_SIZE];
        if (!read_ssid_at_(last, tail_ssid, sizeof(tail_ssid)))    return false;
        if (!read_password_at_(last, tail_pwd, sizeof(tail_pwd)))  return false;
        if (!write_slot_(idx, tail_ssid, tail_pwd))                return false;
    }

    if (!delete_slot_(last)) return false;
    return write_count_(static_cast<uint8_t>(count - 1));
}

bool WifiCredStore::contains(const char* ssid) const
{
    size_t idx = 0;
    return find_index_(ssid, idx);
}

size_t WifiCredStore::count() const
{
    uint8_t value = 0;
    read_count_(value);
    return value;
}

bool WifiCredStore::ssid_at(size_t idx, char* ssid_out, size_t cap) const
{
    if (ssid_out == nullptr || cap == 0) return false;
    if (idx >= count()) return false;
    return read_ssid_at_(idx, ssid_out, cap);
}

bool WifiCredStore::last_ssid(char* out, size_t cap) const
{
    if (out == nullptr || cap == 0) return false;
    return _kv.get_str(LAST_SSID_KEY, out, cap) >= 0;
}

bool WifiCredStore::set_last_ssid(const char* ssid)
{
    if (!valid_ssid_(ssid)) return false;
    return _kv.put_str(LAST_SSID_KEY, ssid);
}

bool WifiCredStore::merge_from(const WifiCredential* arr, size_t n)
{
    if (arr == nullptr && n > 0) return false;
    char current_pwd[WIFI_PASSWORD_BUF_SIZE];
    for (size_t i = 0; i < n; ++i) {
        const char* ssid = arr[i].ssid;
        const char* password = arr[i].password;
        if (get(ssid, current_pwd, sizeof(current_pwd)) &&
            std::strcmp(current_pwd, password) == 0) {
            continue;
        }
        if (!put(ssid, password)) return false;
    }
    return true;
}

bool WifiCredStore::find_index_(const char* ssid, size_t& out_idx) const
{
    if (!valid_ssid_(ssid)) return false;
    const size_t n = count();
    char slot_ssid[WIFI_SSID_BUF_SIZE];
    for (size_t i = 0; i < n; ++i) {
        if (!read_ssid_at_(i, slot_ssid, sizeof(slot_ssid))) continue;
        if (std::strcmp(slot_ssid, ssid) == 0) {
            out_idx = i;
            return true;
        }
    }
    return false;
}

bool WifiCredStore::read_count_(uint8_t& out) const
{
    out = 0;
    if (!_kv.has_key(COUNT_KEY)) return true;     // unset = 0
    return _kv.get_u8(COUNT_KEY, out);
}

bool WifiCredStore::write_count_(uint8_t value)
{
    return _kv.put_u8(COUNT_KEY, value);
}

bool WifiCredStore::read_ssid_at_(size_t idx, char* out, size_t cap) const
{
    char key[KEY_VALUE_STORE_MAX_NAME_SIZE + 1];
    make_ssid_key_(idx, key, sizeof(key));
    return _kv.get_str(key, out, cap) >= 0;
}

bool WifiCredStore::read_password_at_(size_t idx, char* out, size_t cap) const
{
    char key[KEY_VALUE_STORE_MAX_NAME_SIZE + 1];
    make_pwd_key_(idx, key, sizeof(key));
    return _kv.get_str(key, out, cap) >= 0;
}

bool WifiCredStore::write_slot_(size_t idx, const char* ssid, const char* password)
{
    char ssid_key[KEY_VALUE_STORE_MAX_NAME_SIZE + 1];
    char pwd_key[KEY_VALUE_STORE_MAX_NAME_SIZE + 1];
    make_ssid_key_(idx, ssid_key, sizeof(ssid_key));
    make_pwd_key_(idx, pwd_key, sizeof(pwd_key));

    if (!_kv.put_str(ssid_key, ssid))    return false;
    if (!_kv.put_str(pwd_key, password)) return false;
    return true;
}

bool WifiCredStore::delete_slot_(size_t idx)
{
    char ssid_key[KEY_VALUE_STORE_MAX_NAME_SIZE + 1];
    char pwd_key[KEY_VALUE_STORE_MAX_NAME_SIZE + 1];
    make_ssid_key_(idx, ssid_key, sizeof(ssid_key));
    make_pwd_key_(idx, pwd_key, sizeof(pwd_key));

    bool ok = true;
    if (_kv.has_key(ssid_key) && !_kv.remove(ssid_key)) ok = false;
    if (_kv.has_key(pwd_key)  && !_kv.remove(pwd_key))  ok = false;
    return ok;
}
