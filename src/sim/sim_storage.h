#pragma once

#include <filesystem>

bool is_sim_device_uid(const char* device_uid);
bool resolve_sim_store_root(const char* device_uid,
                            const char* store_name,
                            std::filesystem::path& root);
