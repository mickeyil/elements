#pragma once

class Playback;
class AnimationStore;
class KeyValueStore;
class SystemPlatform;
struct DeviceStatus;

// The dependencies inbound commands may touch. CommandHandler holds a
// reference to this bundle and reaches through it for every command.
// Plain struct on purpose: a back-reference to the firmware app would
// reintroduce the monolith this design avoids.
//
// Constructed once at boot; the references point at objects the App
// owns directly.

struct AppContext {
    Playback&       playback;
    AnimationStore& animations;
    KeyValueStore&  profile_kv;   // save/load_hardware_profile target
    DeviceStatus&   status;
    SystemPlatform& system;       // called by the App, never by handlers

    // Set by the Reboot handler. The App's outer loop reboots once the
    // ACK is on the wire; rebooting from inside the handler would cut
    // the ACK off.
    bool reboot_requested = false;

    // The loaded program came from the AnimationStore, not a live
    // LOAD. The App's render loop restarts on Ended while this is set;
    // that restart is what makes local animations loop.
    bool local_program_loaded = false;
};
