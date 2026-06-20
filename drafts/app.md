# The App

How the implemented device stack becomes a running device. Everything
below the App exists in `src/` with tests: transports, discovery, the
controller link, clock sync, playback, the animation store, and the
command stack. The App is the one object that wires them together and
drives them, written once and shared verbatim by the firmware and the
sim. Each platform contributes only a thin shell: a `main` that
constructs its concrete platform pieces, hands them to the App, and
calls `begin()` once and `tick()` forever.

## The boundary

The App class lives in `src/` and owns every shared object: the
`SyncedClock` and `ClockSyncClient`, `Playback`, `AnimationStore`,
`DeviceStatus`, the `AppContext` bundle, `CommandHandler`, and
`ControllerLink`. What it cannot construct itself it takes by
reference, and that constructor list is the platform boundary:

```
NetworkInterface     EspNetworkInterface   / HostNetworkInterface
DiscoveryClient      (shared; sim may configure unicast)
TcpTransport         EspTcpTransport       / PosixTcpTransport
UdpTransport         EspUdpTransport       / PosixUdpTransport
FileStore            EspFileStore          / PosixFileStore
KeyValueStore        NvsKeyValueStore      / FileKeyValueStore
SystemPlatform       EspSystemPlatform     / SimSystemPlatform
FrameOutput          EspFrameOutput        / SimFrameOutput
DeviceIdentity       make_esp_device_identity / make_sim_device_identity
```

Everything that differs at construction time stays in the platform
`main`: the sim resolves its uid, ports, and storage path from argv;
the firmware derives its uid from the chip MAC and its settings from
NVS. Everything that happens per tick is identical on both platforms
and lives in the App. This is the pattern the lower layers already
follow (`ControllerLink` takes seams by reference and owns its
`CommandProcessor`; `AnimationStore` is one concrete class over a
`FileStore`), applied one level up.

The driver loop itself belongs to the platform, because Arduino owns
`loop()`. The firmware calls `app.begin()` from `setup()` and
`app.tick()` from `loop()`; the sim wraps `tick()` in its own `while`
with signal and exit handling, sleeping a millisecond per pass. The
sleep is safe because frame pacing happens inside the App, not in the
driver.

## The presentation seam

Presentation is the one platform difference that is not yet behind an
interface. `FrameOutput` closes it with two methods: apply_profile(),
the one-time setup where the implementation takes its gamma and
channel order from the hardware profile, and write(), called once per
rendered frame to turn the program-space RGB strip into platform
output.

The firmware implementation applies the profile's gamma LUT, copies
RGB into the FastLED buffer in the profile's channel order, zero-pads
trailing pixels when the strip is shorter than `MAX_STRIP_PIXELS`,
and calls `FastLED.show()`. It also measures `slack_us`, the time
left between `FastLED.show()` returning and the frame deadline;
negative or near-zero slack is the telemetry that says a program is
asking too much of the hardware at its declared fps.

The sim implementation sends the frame-preview UDP packet
(`uid[16] + frame_index:u32 + t_program:f32 + rgb...`, port 6042;
see `controller_v3.md`) and keeps its own frame counter. It applies
no gamma: gamma compensates the physical LEDs, and the controller UI
renders on a screen that does its own.

The profile is setup, not per-frame data: it is fixed for the process
lifetime (`SetHardwareProfile` lands through a reboot), so the App
applies it to the output once in `begin()`, right after applying it
to `Playback`, and write() carries only what varies per frame: the
pixels and `t_program`, which the sim packet header needs and the
firmware ignores. The cost of the seam is one virtual call per frame
at playback rates, which is noise next to `FastLED.show()`.

## begin()

Boot order: `network.begin()`, then load the hardware profile from the
key-value store, apply it to `Playback`, and set the `profile_present`
flag in `DeviceStatus`. The animation store builds its playlist index
in its constructor, so by the end of `begin()` the device knows its
strip, its identity, and its local library. Whether a background
animation starts here is the detached-mode policy's call (open item
below).

## tick()

One pass, every iteration of the platform loop:

```
network.poll();
link.poll();
if (ctx.reboot_requested) system.reboot();   // ACK already flushed
sync.set_controller(link.controller_ip_addr());
sync.poll();
// mode transitions on link.is_ready() changes (policy open)
// frame scheduling and presentation (below)
```

The reboot check sits right after `link.poll()` because the Reboot
handler only sets the flag; the link has flushed the ACK by the time
`poll()` returns, so rebooting here is what makes the ACK ordering
hold (see `system_platform.h`).

Frame pacing is the App's job. `Playback::render_next_frame()` renders
on every call once PLAYING; nothing inside it gates against the
program's fps. The App keeps a next-frame deadline and steps it by
`1 / target_fps()` per frame, calling `render_next_frame()` only when
the deadline arrives. A `Rendered` or `Ended` result goes to the
frame output; `Unchanged` does not. The budget the deadline enforces
covers the whole presentation path (gamma, channel conversion, buffer
copy, `FastLED.show()`), not just engine math. Cadence is not a LOAD
admission gate: the firmware runs the program and reports slack, and
the composer reads the telemetry.

On `Ended` with `local_program_loaded` set, the App restarts the
program (`handle_start` is valid from ENDED); that restart is what
makes local animations loop. Whether it repeats one animation or
advances through the playlist is open (below).

## Why this shape

The split maps onto what genuinely differs. Construction differs per
platform, so it lives in the mains; per-tick behavior does not, so it
lives in the App. The alternatives fail in opposite directions: if
the mains own all the objects and the App is just a loop function,
both mains duplicate the wiring, which is exactly the bug-prone part;
if the App owns the concrete platform objects behind `#ifdef`, shared
code grows platform branches and the App stops being constructible on
the host with fakes.

Host testability is the payoff that matters most. The App's
constructor accepts the same fakes the lower layers already test
with (the controllable test clock, the fake transports and discovery
from the link tests, the POSIX stores over a temp directory, plus a
recording `FrameOutput`), so attach/detach transitions, reboot
ordering, frame pacing, and local looping all get Catch2 coverage
before the firmware ever runs them. The open policy questions all
land inside the App without moving the platform boundary; deciding
them later touches one shared file and its tests.

## Open items

- **Detached-mode policy.** `DeviceMode` names the states
  (AttachedControlled, DetachedGraceHold, DetachedBlank,
  DetachedBackground) but no v3 rule says when the App moves between
  them, what plays while detached, or how long a grace hold lasts.
  Until then `tick()` carries a placeholder transition on
  `link.is_ready()` changes.
- **Loss of sync.** Direction is decided (fade to black on lease
  loss, no silent fallback to unsynced playback); recovery when the
  lease returns is not. See the TODO entry.
- **Playlist semantics on Ended.** Repeat the one animation, or
  advance through the play order. The restart mechanics are the same
  either way.
- **Slack telemetry path.** The firmware frame output measures
  `slack_us`; how it travels to the composer (likely extra fields in
  the `QueryDeviceStatus` ACK) is unspecified.
