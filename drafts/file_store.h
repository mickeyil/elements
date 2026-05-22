#pragma once

#include <cstddef>
#include <cstdint>

// A thin wrapper around the platform's file storage. Lets the device
// store and retrieve small named blobs without caring whether it runs
// on ESP (LittleFS) or POSIX (host filesystem).
//
// Names are short flat filenames; there are no directories. Every call
// is single-shot: no open handle is kept across calls. Single-threaded;
// implementations do no internal locking.
//
// Buffers are caller-owned. read() fills a buffer the caller supplies
// and never allocates. This matches TcpTransport / UdpTransport and
// keeps the platform layer allocation-free, which matters on the ESP
// single heap.
//
// Implementations:
//   - PosixFileStore (host/sim, files under a stable directory)
//   - EspFileStore   (firmware, LittleFS)

class FileStore {
public:
    virtual ~FileStore() = default;

    // Create `name`, or atomically replace it if it exists, with `len`
    // bytes from `src`. Atomic: a power loss leaves either the whole
    // old file or the whole new one, never a partial (the impl writes
    // a temp file and renames). Returns false on a backing-store error.
    virtual bool write(const char* name, const uint8_t* src, size_t len) = 0;

    // Byte size of `name`, or < 0 if it does not exist.
    virtual int size(const char* name) const = 0;

    // Read up to `max_len` bytes of `name` into `dst`. Returns the
    // number of bytes read, or < 0 if `name` is absent or the read
    // fails. A small `max_len` peeks a blob header without reading the
    // whole file.
    virtual int read(const char* name, uint8_t* dst, size_t max_len) const = 0;

    // Delete `name`. Returns false if it does not exist or the delete
    // fails.
    virtual bool remove(const char* name) = 0;
};
