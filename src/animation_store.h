#pragma once

#include <cstddef>
#include <cstdint>

#include "file_store.h"

// The device's library of background animations: the unsynced
// animation blobs it plays when not under live controller control.
// One concrete class; every platform difference lives in the FileStore
// it is handed, so this logic is built once and shared by firmware and
// sim, the same way CommandProcessor is shared above TcpTransport.
//
// On-disk layout, all reached through the FileStore:
//   <name>.anim    one file per animation blob, named by the animation.
//   playlist.txt   one line per animation: <name> [<crc32 hex>].
//                  Line order is the play order and is authoritative:
//                  boot and PlayLocalAnimation walk this file. A .anim
//                  file absent from it is ignored; a line naming a
//                  missing file is skipped. The crc is a cached
//                  fingerprint; a line without one (hand-added, or a
//                  crash between blob and playlist writes) gets its crc
//                  recomputed from the blob at boot.
//
// The store keeps an in-memory index of the playlist, built once at
// construction. Boot cost is one small playlist read plus a header
// peek per blob; whole-blob reads happen only on the crc-recompute
// path above.
//
// Identity. An animation is named by a controller-assigned string: the
// stem of its DSL source file (the program_id on the controller side),
// at most ANIM_NAME_SIZE characters. The name is stable across content
// edits, so re-storing under an existing name overwrites that blob in
// place and leaves the play order untouched. That is the update path.
// The crc32 of the blob is the content fingerprint; QueryLocalAnimations
// reports it so the controller can tell whether a stored animation is
// current or stale against its own source.
//
// Names are filesystem-safe by construction: the controller's
// program_id charset is [A-Za-z0-9._-]. The device parse rule is
// stricter; it also rejects a leading '.' (which covers "." and ".."),
// so a name can never act as a path-traversal token.
//
// What store() rejects: an invalid name, a blob too short for the
// 20-byte header or with foreign magic/version, a blob whose header
// flags bit0 (requires_sync) is set (synced animations are live-only,
// never cached), a blob over MAX_BLOB_BYTES (src/link_protocol.h; live
// LOAD and StoreAnimation share one ceiling), a store already full at
// MAX_STORED_ANIMATIONS, and a backing-store error.
//
// Strip-length mismatch is neither rejected nor wiped. A blob built
// for a different strip length than the active profile stays in the
// store; playback selection skips it. Removing it is the operator's
// decision, issued through the controller.

// Most animations the store will hold. Provisional; revisit against
// the real flash budget.
constexpr size_t MAX_STORED_ANIMATIONS = 16;

// Largest animation name, in characters. On the wire it is a fixed
// ANIM_NAME_SIZE-byte ASCII slot, null-padded if shorter.
constexpr size_t ANIM_NAME_SIZE = 32;

// In-memory name buffer: room for the name, a null terminator, and the
// ".anim" suffix, so one buffer size serves names and filenames.
constexpr size_t ANIM_NAME_BUF_SIZE = ANIM_NAME_SIZE + 8;

static_assert(ANIM_NAME_SIZE + sizeof(".anim") <= ANIM_NAME_BUF_SIZE,
              "animation filename must fit the name buffer");
static_assert(ANIM_NAME_SIZE + sizeof(".anim") - 1 <= FILE_STORE_MAX_NAME_SIZE,
              "animation filename must be a valid FileStore name");

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
    size_t count() const { return _count; }

    // Metadata for the animation at play-order position `index`.
    // `index` must be in [0, count()).
    AnimationEntry entry(size_t index) const { return _entries[index]; }

    // Store `blob` under `name`. If `name` is already present its blob
    // is overwritten in place and the play order is unchanged; a new
    // name is appended to the play order. The crc32 is computed and
    // recorded. False on any of the rejections listed above.
    bool store(const char* name, const uint8_t* blob, size_t len);

    // Erase the animation `name` and drop it from the play order.
    // False if no stored animation has that name.
    bool erase(const char* name);

    // Replace the play order. `names` must be a permutation of the
    // currently stored names: it reorders, never adds or removes.
    // False otherwise.
    bool set_order(const char* const* names, size_t n);

    // Read the blob for `name` into `out` (`out_len` bytes, taken from
    // the matching AnimationEntry::blob_len). False on unknown name,
    // size mismatch, or read failure.
    bool read_blob(const char* name, uint8_t* out, size_t out_len) const;

private:
    void load_index_();
    bool write_playlist_();
    int  find_(const char* name) const;  // play-order index, or -1

    FileStore& _files;
    AnimationEntry _entries[MAX_STORED_ANIMATIONS] = {};
    size_t _count = 0;
};
