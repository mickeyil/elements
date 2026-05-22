#pragma once

#include <cstddef>
#include <cstdint>

// Abstract background-blob store. Two impls:
//   - EspBackgroundStore: NVS metadata + LittleFS blob (firmware).
//   - SimBackgroundStore: file at a stable path on disk; survives the
//                         launcher-driven re-exec on sim reboot, the
//                         same way ESP flash survives a chip restart.
//
// CommandHandler depends on this interface, never on a
// platform-specific class. The current BackgroundStore in src/firmware/
// becomes one impl of this.

struct BackgroundMetadata {
    bool     present = false;
    uint16_t strip_length = 0;
    uint32_t blob_len = 0;
    uint32_t crc32 = 0;
};

class BackgroundStore {
public:
    virtual ~BackgroundStore() = default;

    // Snapshot of the currently stored background. present == false when
    // nothing is stored.
    virtual BackgroundMetadata metadata() const = 0;

    // Write blob + metadata. Returns false on backing-store failure or
    // CRC mismatch.
    virtual bool store(
        const uint8_t* blob,
        size_t len,
        uint16_t strip_length,
        uint32_t expected_crc32
    ) = 0;

    // Erase the stored background.
    virtual bool clear() = 0;

    // Read the stored blob into out (out_len bytes). Returns false if no
    // blob is present, sizes don't match, or read fails.
    virtual bool read_blob(uint8_t* out, size_t out_len) const = 0;

    // TODO: sim impl backs the store with a file at a stable path
    // (e.g. XDG runtime dir, /tmp/elements-bg-<uid>) so it survives
    // the launcher-driven re-exec on sim reboot.
};
