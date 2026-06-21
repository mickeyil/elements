#include "command_handler.h"

#include <cstring>
#include <new>

#include "animation_store.h"
#include "app_context.h"
#include "device_status.h"
#include "hardware_profile_store.h"
#include "link_protocol.h"
#include "local_animation.h"
#include "playback.h"
#include "wire_reader.h"
#include "wire_writer.h"

namespace {

AckStatus map_playback_result(PlaybackResult result)
{
    switch (result) {
        case PlaybackResult::Ok:         return AckStatus::Ok;
        case PlaybackResult::WrongState: return AckStatus::WrongState;
        case PlaybackResult::Unsynced:   return AckStatus::Unsynced;
        case PlaybackResult::BadTime:    return AckStatus::BadPayload;
    }
    return AckStatus::Error;
}

AckStatus map_decode_error(DecodeError err)
{
    if (err == DecodeError::Ok) return AckStatus::Ok;
    if (err == DecodeError::StripLengthMismatch) return AckStatus::ProfileMismatch;
    return AckStatus::Error;
}

// Copy the fixed 32-byte wire name slot into a null-terminated buffer;
// false if the payload is too short to hold the slot.
bool read_name_slot(WireReader& r, char (&out)[ANIM_NAME_BUF_SIZE])
{
    const uint8_t* slot = r.take(ANIM_NAME_SIZE);
    if (slot == nullptr) return false;
    std::memset(out, 0, sizeof(out));
    std::memcpy(out, slot, ANIM_NAME_SIZE);
    return true;
}

}  // namespace

CommandHandler::CommandHandler(AppContext& ctx)
    : _ctx(ctx)
{
}

AckStatus CommandHandler::handle(uint8_t opcode,
                                 const uint8_t* payload, size_t payload_len,
                                 WireWriter& reply)
{
    WireReader r(payload, payload_len);

    switch (opcode) {
        case CMD_SET_PROFILE:            return handle_set_profile_(r);
        case CMD_LOAD:                   return handle_load_(r);
        case CMD_START:                  return handle_start_(r);
        case CMD_JUMP:                   return handle_jump_(r);
        case CMD_PAUSE:                  return handle_pause_(r);
        case CMD_RESUME:                 return handle_resume_(r);
        case CMD_STOP:                   return handle_stop_(r);
        case CMD_PLAY_LOCAL_ANIMATION:   return handle_play_local_animation_(r);
        case CMD_STORE_ANIMATION:        return handle_store_animation_(r);
        case CMD_ERASE_ANIMATION:        return handle_erase_animation_(r);
        case CMD_SET_ANIMATION_ORDER:    return handle_set_animation_order_(r);
        case CMD_REBOOT:                 return handle_reboot_(r);
        case CMD_QUERY_DEVICE_STATUS:    return handle_query_device_status_(r, reply);
        case CMD_PING:                   return handle_ping_(r);
        case CMD_QUERY_LOCAL_ANIMATIONS: return handle_query_local_animations_(r, reply);
        default:                         return AckStatus::UnknownCommand;
    }
}

AckStatus CommandHandler::handle_set_profile_(WireReader& r)
{
    uint16_t strip_length = 0;
    if (!r.read_u16(strip_length) || !r.require_empty()) {
        return AckStatus::BadPayload;
    }
    if (!HardwareProfile(strip_length).is_valid()) {
        return AckStatus::BadPayload;
    }

    const HardwareProfile& active = _ctx.playback.hardware_profile();
    if (active.is_valid() && active.strip_length == strip_length) {
        return AckStatus::Ok;
    }

    // Preserve the stored color order and gamma; the wire only carries
    // the strip length.
    HardwareProfile updated(strip_length);
    HardwareProfile stored;
    if (load_hardware_profile(_ctx.profile_kv, stored)) {
        updated = HardwareProfile(strip_length, stored.color_order, stored.gamma);
    }
    if (!save_hardware_profile(_ctx.profile_kv, updated)) {
        return AckStatus::Error;
    }
    _ctx.reboot_requested = true;
    return AckStatus::Ok;
}

AckStatus CommandHandler::handle_load_(WireReader& r)
{
    const size_t blob_len = r.remaining();
    const uint8_t* blob = r.take(blob_len);
    if (blob == nullptr) {
        return AckStatus::BadPayload;
    }
    if (!_ctx.playback.has_hardware_profile()) {
        return AckStatus::WrongState;
    }

    DecodeError err = DecodeError::Ok;
    if (!_ctx.playback.handle_load(blob, blob_len, &err)) {
        return map_decode_error(err);
    }
    _ctx.local_program_loaded = false;
    return AckStatus::Ok;
}

AckStatus CommandHandler::handle_start_(WireReader& r)
{
    int64_t program_start_us = 0;
    if (!r.read_i64(program_start_us) || !r.require_empty()) {
        return AckStatus::BadPayload;
    }
    return map_playback_result(_ctx.playback.handle_start(program_start_us));
}

AckStatus CommandHandler::handle_jump_(WireReader& r)
{
    float t_program = 0.0f;
    if (!r.read_f32(t_program) || !r.require_empty()) {
        return AckStatus::BadPayload;
    }
    return map_playback_result(_ctx.playback.handle_jump(t_program));
}

AckStatus CommandHandler::handle_pause_(WireReader& r)
{
    if (!r.require_empty()) {
        return AckStatus::BadPayload;
    }
    _ctx.playback.handle_pause();
    return AckStatus::Ok;
}

AckStatus CommandHandler::handle_resume_(WireReader& r)
{
    int64_t program_start_us = 0;
    if (!r.read_i64(program_start_us) || !r.require_empty()) {
        return AckStatus::BadPayload;
    }
    return map_playback_result(_ctx.playback.handle_resume(program_start_us));
}

AckStatus CommandHandler::handle_stop_(WireReader& r)
{
    if (!r.require_empty()) {
        return AckStatus::BadPayload;
    }
    _ctx.playback.handle_stop();
    return AckStatus::Ok;
}

AckStatus CommandHandler::handle_play_local_animation_(WireReader& r)
{
    uint16_t order_index = 0;
    if (!r.read_u16(order_index) || !r.require_empty()) {
        return AckStatus::BadPayload;
    }
    // The load-and-start path is shared with the App's detach / boot background
    // policy (local_animation.cpp); map its neutral outcome back to the wire.
    const LocalPlayOutcome out = play_stored_animation(_ctx, order_index);
    switch (out.status) {
        case LocalPlayOutcome::Status::Ok:           return AckStatus::Ok;
        case LocalPlayOutcome::Status::NoProfile:    return AckStatus::WrongState;
        case LocalPlayOutcome::Status::NoSuchEntry:  return AckStatus::BadPayload;
        case LocalPlayOutcome::Status::Error:        return AckStatus::Error;
        case LocalPlayOutcome::Status::DecodeFailed: return map_decode_error(out.decode_err);
        case LocalPlayOutcome::Status::StartFailed:  return map_playback_result(out.start_result);
    }
    return AckStatus::Error;  // unreachable
}

AckStatus CommandHandler::handle_store_animation_(WireReader& r)
{
    char name[ANIM_NAME_BUF_SIZE];
    if (!read_name_slot(r, name)) {
        return AckStatus::BadPayload;
    }
    const size_t blob_len = r.remaining();
    const uint8_t* blob = r.take(blob_len);
    if (blob == nullptr) {
        return AckStatus::BadPayload;
    }
    return _ctx.animations.store(name, blob, blob_len) ? AckStatus::Ok
                                                       : AckStatus::Error;
}

AckStatus CommandHandler::handle_erase_animation_(WireReader& r)
{
    char name[ANIM_NAME_BUF_SIZE];
    if (!read_name_slot(r, name) || !r.require_empty()) {
        return AckStatus::BadPayload;
    }
    return _ctx.animations.erase(name) ? AckStatus::Ok : AckStatus::Error;
}

AckStatus CommandHandler::handle_set_animation_order_(WireReader& r)
{
    uint16_t count = 0;
    if (!r.read_u16(count) || count > MAX_STORED_ANIMATIONS) {
        return AckStatus::BadPayload;
    }

    char names[MAX_STORED_ANIMATIONS][ANIM_NAME_BUF_SIZE];
    const char* name_ptrs[MAX_STORED_ANIMATIONS];
    for (size_t i = 0; i < count; ++i) {
        if (!read_name_slot(r, names[i])) {
            return AckStatus::BadPayload;
        }
        name_ptrs[i] = names[i];
    }
    if (!r.require_empty()) {
        return AckStatus::BadPayload;
    }
    return _ctx.animations.set_order(name_ptrs, count) ? AckStatus::Ok
                                                       : AckStatus::Error;
}

AckStatus CommandHandler::handle_reboot_(WireReader& r)
{
    if (!r.require_empty()) {
        return AckStatus::BadPayload;
    }
    _ctx.reboot_requested = true;
    return AckStatus::Ok;
}

AckStatus CommandHandler::handle_query_device_status_(WireReader& r, WireWriter& w)
{
    if (!r.require_empty()) {
        return AckStatus::BadPayload;
    }
    w.write_u8(static_cast<uint8_t>(_ctx.status.mode));
    w.write_u8(_ctx.status.flags);
    w.write_u16(static_cast<uint16_t>(_ctx.animations.count()));
    return w.ok() ? AckStatus::Ok : AckStatus::Error;
}

AckStatus CommandHandler::handle_ping_(WireReader& r)
{
    return r.require_empty() ? AckStatus::Ok : AckStatus::BadPayload;
}

AckStatus CommandHandler::handle_query_local_animations_(WireReader& r, WireWriter& w)
{
    if (!r.require_empty()) {
        return AckStatus::BadPayload;
    }

    const size_t count = _ctx.animations.count();
    w.write_u16(static_cast<uint16_t>(count));
    for (size_t i = 0; i < count; ++i) {
        const AnimationEntry entry = _ctx.animations.entry(i);
        uint8_t slot[ANIM_NAME_SIZE] = {};
        std::memcpy(slot, entry.name, std::strlen(entry.name));
        w.write_bytes(slot, sizeof(slot));
        w.write_u16(entry.strip_length);
        w.write_u32(entry.crc32);
    }
    return w.ok() ? AckStatus::Ok : AckStatus::Error;
}
