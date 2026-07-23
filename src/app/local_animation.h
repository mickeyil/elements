#pragma once

#include <cstdint>

#include "core/blob_reader.h"   // DecodeError
#include "app/playback.h"      // PlaybackResult

struct AppContext;

// The outcome of loading and starting a stored animation locally. Neutral on
// purpose, so both the PLAY_LOCAL_ANIMATION command (which maps it back to an
// AckStatus) and the App's detach / boot background policy (which only needs
// success or failure) share one load-and-start path.
struct LocalPlayOutcome
{
    enum class Status : uint8_t
    {
        Ok,
        NoProfile,     // no hardware profile applied yet
        NoSuchEntry,   // order_index is past the end of the store
        Error,         // allocation or storage-read failure
        DecodeFailed,  // the blob did not decode (see decode_err)
        StartFailed,   // playback refused the start (see start_result)
    };

    Status         status;
    DecodeError    decode_err   = DecodeError::Ok;     // when DecodeFailed
    PlaybackResult start_result = PlaybackResult::Ok;  // when StartFailed

    bool ok() const { return status == Status::Ok; }
};

// Load the stored animation at order_index and start it as an unsynced local
// program from time 0, setting ctx.local_program_loaded on success. Stored
// blobs are unsynced by construction (the store rejects requires_sync), so the
// start can never return Unsynced.
LocalPlayOutcome play_stored_animation(AppContext& ctx, uint16_t order_index);
