#pragma once

#include <cstddef>
#include <cstdint>

#include "../src/file_store.h"

// The device's library of background animations: the unsynced
// animation blobs it plays when not under live controller control.
// One concrete class; every platform difference lives in the FileStore
// it is handed, so this logic is built once and shared by firmware and
// sim, the same way CommandProcessor is shared above TcpTransport.
//
// On-disk layout, all reached through the FileStore:
//   <name>.anim    one file per animation blob, named by the animation.
//   playlist.txt   the play order, one <name> per line. Authoritative:
//                  boot and PlayLocalAnimation walk this file. A
//                  .anim file absent from it is ignored; a line naming
//                  a missing file is skipped.
//
// Identity. An animation is named by a controller-assigned string: the
// stem of its DSL source file (the program_id on the controller side),
// at most ANIM_NAME_SIZE characters. The name is stable across content
// edits, so re-storing under an existing name overwrites that blob in
// place and leaves the play order untouched. That is the update path.
// The crc32 of the blob is recorded separately as a content
// fingerprint; QueryLocalAnimations reports it so the controller can
// tell whether a stored animation is current or stale against its own
// source.
//
// Names are filesystem-safe by construction: the controller's
// program_id charset is [A-Za-z0-9._-]. The device parse rule is
// stricter; it also rejects ".", "..", and a leading '.', so a name
// can never act as a path-traversal token.
//
// What store() rejects. It reads the blob's 20-byte header and rejects
// a blob whose flags bit0 (requires_sync) is set: synced animations
// are live-only, never cached. It also rejects an invalid name, a blob
// over MAX_STORED_BLOB_BYTES, and a store already full at
// MAX_STORED_ANIMATIONS (or a backing-store error).
//
// Strip-length mismatch is neither rejected nor wiped. A blob built
// for a different strip length than the active profile stays in the
// store; playback selection skips it. Removing it is the operator's
// decision, issued through the controller.

// Most animations the store will hold. Draft figure; revisit against
// the real flash budget.
constexpr size_t MAX_STORED_ANIMATIONS = 16;

// Largest single stored-animation blob. Same as the wire cap
// MAX_BLOB_BYTES (src/link_protocol.h): live LOAD and stored
// StoreAnimation carry the same kind of artifact, so they share one
// ceiling. Also bounds the transient std::vector a load allocates to
// stage the blob for the decoder.
constexpr size_t MAX_STORED_BLOB_BYTES = 16 * 1024;

// Largest animation name, in characters. On the wire it is a fixed
// ANIM_NAME_SIZE-byte ASCII slot, null-padded if shorter.
constexpr size_t ANIM_NAME_SIZE = 32;

// In-memory name buffer: ANIM_NAME_SIZE plus slack for a null
// terminator, so a name can be used directly as a C string / filename.
constexpr size_t ANIM_NAME_BUF_SIZE = ANIM_NAME_SIZE + 8;

struct AnimationEntry {
    char     name[ANIM_NAME_BUF_SIZE];  // null-terminated; the identity
    uint16_t strip_length;              // blob-header strip length
    uint32_t blob_len;
    uint32_t crc32;                     // content fingerprint
};

class AnimationStore {
public:
    explicit AnimationStore(FileStore& files);

    // Number of stored animations.
    size_t count() const;

    // Metadata for the animation at play-order position `index`.
    // `index` must be in [0, count()).
    AnimationEntry entry(size_t index) const;

    // Store `blob` under `name`. If `name` is already present its blob
    // is overwritten in place and the play order is unchanged; a new
    // name is appended to the play order. The crc32 is computed and
    // recorded. Returns false if `name` is invalid, the blob requires
    // sync, exceeds MAX_STORED_BLOB_BYTES, or does not fit.
    bool store(const char* name, const uint8_t* blob, size_t len);

    // Erase the animation `name` and drop it from the play order.
    // Returns false if no stored animation has that name.
    bool erase(const char* name);

    // Replace the play order. `names` must be a permutation of the
    // currently stored names: it reorders, never adds or removes.
    // Returns false otherwise.
    bool set_order(const char* const* names, size_t n);

    // Read the blob for `name` into `out` (`out_len` bytes, taken from
    // the matching AnimationEntry::blob_len). Returns false on unknown
    // name, size mismatch, or read failure.
    bool read_blob(const char* name, uint8_t* out, size_t out_len) const;

private:
    FileStore& _files;
};
