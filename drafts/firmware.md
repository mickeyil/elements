# Firmware and Engine

A device receives a compiled animation blob over TCP, decodes it, and
plays it back on a WS2812B LED strip. The engine and almost all the
device code are shared C++ that runs unchanged on a real ESP32 and in
the host simulator; only a thin layer of platform code differs.

## Pipeline

```
blob (from the controller)
  -> decoder      parse the bytes into a Program
    -> engine     walk the timeline, run animations
      -> animations  paint HSVA pixels into working buffers
        -> compositor  blend the buffers, convert to RGB, apply gamma
          -> strip output  (real LEDs, or a preview packet)
```

## Core pieces

**Program** — everything decoded from one blob: the layers, the working
pixel memory, the duration, and the target frame rate. One is loaded at
a time; loading a new one replaces the old.

**Layer** — a stack of animations that play on the same pixels at
different times. Layers blend bottom to top, so a higher layer draws over
a lower one. Animations on one layer never overlap in time.

**Animation** — one effect, created when its moment arrives and dropped
when it ends. There are four kinds:

| Kind | What it does |
|------|--------------|
| wave  | smooth sine modulation of one color channel |
| spark | a flash that fades out |
| paint | a solid color, or a color per pixel |
| shift | slides pixels along the strip from a starting snapshot |

**Compositor** — blends the active layers into the final RGB frame, using
each pixel's own alpha, then applies gamma correction.

**Engine** — owns the Program and the compositor. Each frame it advances
a cursor through the timeline, starts and ends animations as their times
pass, runs any scheduled copies that move pixels between buffers, and
renders. The cursor only moves forward, so a frame costs the same no
matter how long the show is.

Animations that need to read another animation's output get it through
the copies the compiler scheduled, not by reaching across layers. The
byte format that carries all of this is `../docs/blob_format.md`.

## The App

The App is the one object that ties everything together: the clock and
sync client, playback, the animation store, the controller link, and the
command handler. It is written once and shared by the firmware and the
simulator.

Each platform supplies only a thin `main` that builds its concrete
pieces, hands them to the App, and then calls `begin()` once and `tick()`
forever. The pieces that differ per platform are passed in by reference:

```
network, TCP, UDP        ESP / POSIX sockets
file + key-value store   flash (LittleFS / NVS) / host files
clock and system         ESP timers / host clock
frame output             FastLED strip / preview packet
device identity          chip MAC / chosen uid
```

`begin()` brings up the network, loads the saved strip profile, and
applies it. `tick()` runs once per loop pass: poll the network and the
link, reboot if a command asked for it, run a sync round, and, when the
next frame is due, render and present it. The App paces frames against
the program's target rate; nothing downstream has to.

### Frame output

Turning a rendered frame into something visible is the one job that
differs per platform, behind a small `FrameOutput` seam:

- On the **ESP32**, it applies the gamma table, copies the pixels into
  the FastLED buffer in the strip's color order, and calls
  `FastLED.show()`.
- On the **simulator**, it sends the frame as a preview packet to the
  controller (see `protocol.md`). It applies no gamma, since the
  screen does its own.

## Device modes

A device is always in one of a few modes that decide what is on the strip
independent of playback:

- **attached_controlled** — a controller owns playback.
- **detached_grace_hold** — the controller was lost; hold the last frame
  briefly.
- **detached_blank** — go dark.
- **detached_background** — play a stored animation locally.

The exact rules for moving between the detached modes are still being
settled; see `app.md`.

## Local animations

A device can store animations in its own flash and play them with no
controller present. The controller sends them with `STORE_ANIMATION`,
removes them with `ERASE_ANIMATION`, and sets their order with
`SET_ANIMATION_ORDER`; `PLAY_LOCAL_ANIMATION` plays one by its place in
that order, looping when it ends. Each stored animation keeps a CRC so
the controller can tell whether its copy is current. Stored animations
are always unsynced, so they can play standalone.

## Files

Shared core (`src/`), used by both firmware and simulator:

| File | Role |
|------|------|
| `app.{h,cpp}` | wires everything together and drives the loop |
| `playback.{h,cpp}` | the playback state machine and frame rendering |
| `decoder.{h,cpp}` | turns a blob into a Program |
| `engine.{h,cpp}` | timeline cursor and animation lifecycle |
| `compositor.{h,cpp}` | layer blending, HSV to RGB, gamma |
| `animation.h`, `animations/*` | the base class and the four kinds |
| `colors.{h,cpp}` | color types and conversion |
| `controller_link.{h,cpp}` | the TCP link to the controller |
| `command_handler.{h,cpp}` | runs each command, builds the reply |
| `clock_sync_client.{h,cpp}`, `synced_clock.h` | clock sync |
| `discovery.{h,cpp}` | finding the controller |
| `animation_store.{h,cpp}` | stored local animations |
| `hardware_profile.h` | strip length and color order |

Platform code: `src/firmware/` holds the ESP32 pieces (FastLED output,
WiFi, sockets, NVS, flash) and `main.cpp`; `src/sim/` holds the host
pieces (preview output, POSIX sockets, file-backed storage) and its own
`main.cpp`.

## Tests

Catch2 tests live in `test/`. Some test fixtures are generated from the
Python compiler, so regenerate them after changing compiler output.

```bash
cmake -B build && cmake --build build && cd build && ctest
```
