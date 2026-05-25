#pragma once

class Playback;
class AnimationStore;
class KeyValueStore;
class SystemPlatform;
struct DeviceStatus;

// The dependencies inbound commands are allowed to touch. CommandHandler
// holds a reference to this bundle and reaches through it for every
// command. Plain struct on purpose: making it a back-reference to the
// firmware app would reintroduce the monolith this design avoids.
//
// Lifetime: constructed once at boot, lives as long as the program. All
// references point at objects the App owns directly.
//
// reboot_requested is the cross-layer signal from CommandHandler to
// the App. The reboot handler sets it; the App's outer loop reads it
// after CommandProcessor::poll() returns and calls system.reboot()
// once the ACK is on the wire.

struct AppContext {
    Playback&       playback;
    AnimationStore& animations;
    KeyValueStore&  profile_kv;   // save/load_hardware_profile target
    DeviceStatus&   status;
    SystemPlatform& system;

    bool reboot_requested = false;
};
