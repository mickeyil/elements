#include "sim/sim_device_identity.h"

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

DeviceIdentity make_sim_device_identity(const char* uid)
{
    DeviceIdentity identity;
    std::memcpy(identity.uid, uid, strnlen(uid, UID_SIZE));
    identity.boot_token = make_sim_boot_token_();
    return identity;
}
