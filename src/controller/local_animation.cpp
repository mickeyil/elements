#include "controller/local_animation.h"

#include <new>

#include "controller/animation_store.h"
#include "controller/app_context.h"
#include "controller/playback.h"

LocalPlayOutcome play_stored_animation(AppContext& ctx, uint16_t order_index)
{
    using Status = LocalPlayOutcome::Status;

    if (!ctx.playback.has_hardware_profile()) {
        return {Status::NoProfile};
    }
    if (order_index >= ctx.animations.count()) {
        return {Status::NoSuchEntry};
    }

    const AnimationEntry entry = ctx.animations.entry(order_index);
    uint8_t* blob = new (std::nothrow) uint8_t[entry.blob_len];
    if (blob == nullptr) {
        return {Status::Error};
    }
    if (!ctx.animations.read_blob(entry.name, blob, entry.blob_len)) {
        delete[] blob;
        return {Status::Error};
    }

    DecodeError err = DecodeError::Ok;
    const bool loaded = ctx.playback.handle_load(blob, entry.blob_len, &err);
    delete[] blob;
    if (!loaded) {
        return {Status::DecodeFailed, err};
    }

    const PlaybackResult started = ctx.playback.handle_start(0);
    if (started != PlaybackResult::Ok) {
        return {Status::StartFailed, DecodeError::Ok, started};
    }

    ctx.local_program_loaded = true;
    return {Status::Ok};
}
