#pragma once

#include <cstddef>
#include <cstdint>

constexpr size_t FILE_STORE_MAX_NAME_SIZE = 48;
constexpr size_t FILE_STORE_PATH_BUF_SIZE = 64;
constexpr char FILE_STORE_TMP_SUFFIX[] = ".tmp";

static_assert(
    1 + FILE_STORE_MAX_NAME_SIZE + sizeof(FILE_STORE_TMP_SUFFIX)
        <= FILE_STORE_PATH_BUF_SIZE,
    "FileStore path buffer too small"
);

enum class FileStoreState : uint8_t {
    NotReady = 0,
    Ready,
    Faulted,
};

// A thin wrapper around platform file storage. Stores small flat files
// without exposing LittleFS or host filesystem details to shared code.
//
// Each call opens and closes whatever it needs. Single-threaded;
// concrete stores do no internal locking.

class FileStore {
public:
    virtual ~FileStore() = default;

    virtual FileStoreState state() const = 0;

    // Create name, or atomically replace it, with len bytes from src.
    virtual bool write(const char* name, const uint8_t* src, size_t len) = 0;

    // Byte size of name, or < 0 if it does not exist.
    virtual int size(const char* name) = 0;

    // Read up to max_len bytes of name into dst.
    virtual int read(const char* name, uint8_t* dst, size_t max_len) = 0;

    // Delete name. Returns false if it does not exist.
    virtual bool remove(const char* name) = 0;
};
