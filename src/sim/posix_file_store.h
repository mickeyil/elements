#pragma once

#include <filesystem>

#include "file_store.h"

// Host filesystem backing for FileStore. Each sim device gets a stable
// directory under $ELEMENTS_SIM_STORAGE_ROOT/simstorage.

class PosixFileStore : public FileStore {
public:
    explicit PosixFileStore(const char* device_uid);

    FileStoreState state() const override { return _state; }
    bool write(const char* name, const uint8_t* src, size_t len) override;
    int size(const char* name) override;
    int read(const char* name, uint8_t* dst, size_t max_len) override;
    bool remove(const char* name) override;

private:
    bool ensure_ready_();

    std::filesystem::path _root;
    FileStoreState _state = FileStoreState::NotReady;
};
