#include "app/hardware_profile_store.h"

#include "core/gamma.h"

namespace {

constexpr char STRIP_LENGTH_KEY[] = "strip_len";
constexpr char COLOR_ORDER_KEY[] = "color_order";
constexpr char GAMMA_KEY[] = "gamma";

bool is_valid_color_order_(ColorOrder order)
{
    return order == ColorOrder::RGB || order == ColorOrder::BGR;
}

bool is_valid_profile_(const HardwareProfile& profile)
{
    return profile.is_valid() &&
           is_valid_color_order_(profile.color_order) &&
           profile.gamma >= IDENTITY_GAMMA &&
           profile.gamma <= MAX_SUPPORTED_GAMMA;
}

}  // namespace

bool load_hardware_profile(KeyValueStore& store, HardwareProfile& out)
{
    uint16_t strip_length = 0;
    uint8_t color_order = 0;
    float gamma = IDENTITY_GAMMA;

    if (!store.get_u16(STRIP_LENGTH_KEY, strip_length)) return false;
    if (!store.get_u8(COLOR_ORDER_KEY, color_order)) return false;
    if (!store.get_f32(GAMMA_KEY, gamma)) return false;

    const HardwareProfile profile(
        strip_length,
        static_cast<ColorOrder>(color_order),
        gamma
    );
    if (!is_valid_profile_(profile)) return false;

    out = profile;
    return true;
}

bool save_hardware_profile(KeyValueStore& store, const HardwareProfile& profile)
{
    if (!is_valid_profile_(profile)) return false;

    if (!store.put_u16(STRIP_LENGTH_KEY, profile.strip_length)) return false;
    if (!store.put_u8(COLOR_ORDER_KEY, static_cast<uint8_t>(profile.color_order))) {
        return false;
    }
    return store.put_f32(GAMMA_KEY, profile.gamma);
}
