#pragma once

#include "platform/file_store.h"

// LittleFS backing for FileStore. The first file operation mounts the
// filesystem and keeps it mounted for the lifetime of this object.

class EspFileStore : public FileStore {
public:
    FileStoreState state() const override { return _state; }
    bool write(const char* name, const uint8_t* src, size_t len) override;
    int size(const char* name) override;
    int read(const char* name, uint8_t* dst, size_t max_len) override;
    bool remove(const char* name) override;

private:
    bool ensure_ready_();

    FileStoreState _state = FileStoreState::NotReady;
};
