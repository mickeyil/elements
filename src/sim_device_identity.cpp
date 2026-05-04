#include "sim_device_identity.h"

#include <cstring>
#include <random>

namespace {

uint32_t make_sim_boot_token_()
{
    std::random_device random;
    uint32_t token =
        (static_cast<uint32_t>(random()) << 16) ^ static_cast<uint32_t>(random());
    if (token == 0) {
        token = 1;
    }
    return token;
}

}  // namespace

bool make_sim_device_identity(const char* uid, DeviceIdentity* out)
{
    if (uid == nullptr || out == nullptr) {
        return false;
    }

    const size_t uid_len = std::strlen(uid);
    if (uid_len == 0 || uid_len > UID_WIRE_SIZE) {
        return false;
    }

    DeviceIdentity identity;
    std::memcpy(identity.uid, uid, uid_len);
    identity.boot_token = make_sim_boot_token_();
    *out = identity;
    return true;
}
