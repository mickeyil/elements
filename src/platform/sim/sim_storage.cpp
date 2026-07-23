#include "platform/sim/sim_storage.h"

#include <cstdlib>

namespace {

constexpr char STORAGE_ROOT_ENV[] = "ELEMENTS_SIM_STORAGE_ROOT";

}  // namespace

bool is_sim_device_uid(const char* device_uid)
{
    if (device_uid == nullptr || *device_uid == '\0') return false;
    // First char must be alphanumeric; avoids "." and ".." path tokens.
    if (!((*device_uid >= 'A' && *device_uid <= 'Z') ||
          (*device_uid >= 'a' && *device_uid <= 'z') ||
          (*device_uid >= '0' && *device_uid <= '9'))) {
        return false;
    }

    for (const char* p = device_uid; *p != '\0'; ++p) {
        const char c = *p;
        if ((c >= 'A' && c <= 'Z') ||
            (c >= 'a' && c <= 'z') ||
            (c >= '0' && c <= '9') ||
            c == '-' || c == '_' || c == '.') {
            continue;
        }
        return false;
    }
    return true;
}

bool resolve_sim_store_root(const char* device_uid,
                            const char* store_name,
                            std::filesystem::path& root)
{
    if (!is_sim_device_uid(device_uid)) return false;
    if (store_name == nullptr || *store_name == '\0') return false;

    const char* base = std::getenv(STORAGE_ROOT_ENV);
    if (base == nullptr || *base == '\0') {
        base = "local";
    }
    root = std::filesystem::path(base) / "simstorage" / device_uid / store_name;
    return true;
}
