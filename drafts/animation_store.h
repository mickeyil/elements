#pragma once

#include <cstddef>
#include <cstdint>

#include "file_store.h"

// The device's library of background animations: the unsynced
// animation blobs it plays when not under live controller control.
// One concrete class; every platform difference lives in the FileStore
// it is handed, so this logic is built once and shared by firmware and
// sim, the same way CommandParser is shared above TcpTransport.
//
// On-disk layout, all reached through the FileStore:
//   <crc32>.anim   one file per animation blob, named by its crc32.
//   playlist.txt   the play order, one <crc32> per line. Authoritative:
//                  boot and PlayLocalAnimation walk this file. An
//                  .anim file absent from it is ignored; a line naming
//                  a missing file is skipped.
//
// Identity. An animation is named by the crc32 of its blob. Storing
// the same bytes twice is idempotent (same crc, same filename, no
// duplicate). The controller reconciles its intent against the device
// by comparing crc lists, keeping no per-device bookkeeping.
//
// What store() rejects. It reads the blob's 20-byte header and rejects
// a blob whose flags bit0 (requires_sync) is set: synced animations
// are live-only, never cached. It also rejects a blob that fails its
// crc check, exceeds MAX_STORED_BLOB_BYTES, or does not fit (store
// full at MAX_STORED_ANIMATIONS, or a backing-store error).
//
// Strip-length mismatch is neither rejected nor wiped. A blob built
// for a different strip length than the active profile stays in the
// store; playback selection skips it. Removing it is the operator's
// decision, issued through the controller.

// Most animations the store will hold. Draft figure; revisit against
// the real flash budget.
constexpr size_t MAX_STORED_ANIMATIONS = 16;

// Largest single stored-animation blob. Well below the 256 KiB wire
// cap: it bounds the transient std::vector a load allocates to stage
// the blob for the decoder. Draft figure.
constexpr size_t MAX_STORED_BLOB_BYTES = 64 * 1024;

struct AnimationEntry {
    uint32_t id;            // crc32 of the blob; stable identifier
    uint16_t strip_length;  // blob-header strip length; for profile match
    uint32_t blob_len;
};

class AnimationStore {
public:
    explicit AnimationStore(FileStore& files);

    // Number of stored animations.
    size_t count() const;

    // Metadata for the animation at play-order position `index`.
    // `index` must be in [0, count()).
    AnimationEntry entry(size_t index) const;

    // Store `blob` as a new animation and append it to the play order.
    // The id is the blob's crc32, so storing identical bytes again is
    // an idempotent no-op. Returns false if the blob requires sync,
    // fails its crc, is too large, or does not fit.
    bool store(const uint8_t* blob, size_t len);

    // Erase animation `id` and drop it from the play order. Returns
    // false if no stored animation has that id.
    bool erase(uint32_t id);

    // Replace the play order. `ids` must be a permutation of the
    // currently stored ids: it reorders, never adds or removes.
    // Returns false otherwise.
    bool set_order(const uint32_t* ids, size_t n);

    // Read the blob for `id` into `out` (`out_len` bytes, taken from
    // the matching AnimationEntry::blob_len). Returns false on unknown
    // id, size mismatch, or read failure.
    bool read_blob(uint32_t id, uint8_t* out, size_t out_len) const;

private:
    FileStore& _files;
};
