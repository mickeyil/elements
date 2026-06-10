#pragma once

#include <filesystem>

// Path resolution for sim device storage. Stores live under
// $ELEMENTS_SIM_STORAGE_ROOT/simstorage/<uid>/<store_name>; when the
// env var is unset, the root falls back to local/ (gitignored), so a
// bare manual run never litters the repo.

bool is_sim_device_uid(const char* device_uid);
bool resolve_sim_store_root(const char* device_uid,
                            const char* store_name,
                            std::filesystem::path& root);
