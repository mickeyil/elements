#include "platform/sim/sim_device_identity.h"

#include <cstring>
#include <random>

namespace {

uint32_t make_sim_boot_token_()
{
    std::random_device random;
    uint32_t token = static_cast<uint32_t>(random());
    if (token == 0) {
        token = 1;
    }
    return token;
}

}  // namespace

DeviceIdentity make_sim_device_identity(const char* uid, const char* version)
{
    DeviceIdentity identity;
    std::memcpy(identity.uid, uid, strnlen(uid, UID_SIZE));
    identity.boot_token = make_sim_boot_token_();
    std::memcpy(identity.version, version, strnlen(version, VERSION_BUF_SIZE - 1));
    return identity;
}
