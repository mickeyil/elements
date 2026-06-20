#pragma once

#include "hardware_profile.h"
#include "key_value_store.h"

constexpr char HARDWARE_PROFILE_KV_NAMESPACE[] = "profile";

bool load_hardware_profile(KeyValueStore& store, HardwareProfile& out);
bool save_hardware_profile(KeyValueStore& store, const HardwareProfile& profile);
