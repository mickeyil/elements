# Elements v2 — Design Document

## Overview

Elements v2 is a rewrite of the elements LED control system. The target hardware is ESP32 (replacing the original ESP8266). The device drives a WS2812B (NeoPixel) LED strip and operates in two modes:

1. **Ambient mode** — standalone playback of pre-programmed, chill background animations
2. **Music sync mode** — precise beat-synced animations driven by a base station that performs audio analysis and compiles animation timelines

The core design principle: **tight timing sync over complex rendering**. Simple animations that land exactly on the beat are more impressive than complex effects with sloppy timing.

---

## System Architecture

```
┌─────────────────────────────────────┐
│          Base Station               │
│  (NUC / Raspberry Pi / Linux box)   │
│                                     │
│  ┌──────────┐  ┌────────────────┐   │
│  │ NTP      │  │ Audio Engine   │   │
│  │ Server   │  │ (ALSA/Pipe)    │   │
│  │ (chrony) │  │                │   │
│  └──────────┘  └───────┬────────┘   │
│                        │            │
│  ┌─────────────────────┴─────────┐  │
│  │ Beat Analyzer + Compiler      │  │
│  │ (offline analysis → bytecode) │  │
│  └───────────────┬───────────────┘  │
│                  │                  │
│  ┌───────────────┴───────────────┐  │
│  │ MQTT Broker (mosquitto)       │  │
│  └───────────────┬───────────────┘  │
└──────────────────┼──────────────────┘
                   │ WiFi (same LAN)
┌──────────────────┼──────────────────┐
│  ESP32 Device    │                  │
│                  │                  │
│  ┌───────────────┴───────────────┐  │
│  │ MQTT Client                   │  │
│  │ (config, programs, commands)  │  │
│  └───────────────┬───────────────┘  │
│                  │                  │
│  ┌───────────────┴───────────────┐  │
│  │ NTP Client → base station     │  │
│  │ (ms-precision fork)           │  │
│  └───────────────┬───────────────┘  │
│                  │                  │
│  ┌───────────────┴───────────────┐  │
│  │ Animation VM                  │  │
│  │ (bytecode interpreter)        │  │
│  │ renders to pixel buffer       │  │
│  └───────────────┬───────────────┘  │
│                  │                  │
│  ┌───────────────┴───────────────┐  │
│  │ FastLED → WS2812B strip       │  │
│  └───────────────────────────────┘  │
└─────────────────────────────────────┘
```

### Network Assumptions

- Base station and ESP32 share the same WiFi network
- No firewall or routing restrictions between them
- Base station runs NTP server + MQTT broker
- Future: potentially multiple ESP32 devices synced to the same base station

---

## Operating Modes

### Ambient Mode

- Device powers on and immediately begins playing pre-stored animation programs
- Programs are stored on device flash (LittleFS/SPIFFS)
- Can be updated via MQTT without reflashing firmware
- Think: slow color waves, gentle breathing, gradient sweeps
- Multiple programs stored, selectable via MQTT command

### Music Sync Mode

The base station orchestrates everything:

1. **Pre-analysis:** Before playback, the base station analyzes the audio track — beat detection, energy analysis, possibly frequency band decomposition (bass, mids, treble)
2. **Compilation:** Analysis results are compiled into a bytecode animation program — a compact binary timeline referencing built-in animation primitives
3. **Upload:** The compiled program is sent to the ESP32 via MQTT before playback begins
4. **Playback trigger:** Base station sends a "start at T0" command, where T0 is an absolute NTP-synced timestamp accounting for audio pipeline latency
5. **Rendering:** ESP32's animation VM walks the program against its NTP-synced clock

The ESP32 is deliberately "dumb" in this mode — it receives a pre-compiled buffer and plays it back. All intelligence (beat analysis, animation design, compilation) lives on the base station where compute is abundant.

---

## Time Synchronization

### Requirements

- Audio-visual sync tolerance: **±10ms** (human perception threshold for beat-sync is ~20ms; we target half that for margin)
- Must hold sync over the duration of a song (3-7 minutes)
- No custom sync protocol — use NTP pointed at the base station

### Solution: NTP on LAN

The base station runs a standard NTP server (chrony or ntpd). The ESP32 runs a forked NTPClient library with millisecond-precision support (extracts NTP fractional seconds field, bytes 44-47 of the NTP response).

**Why NTP is sufficient:**
- On a local WiFi network with no hops, NTP achieves ±1-5ms accuracy
- This is well within the ±10ms budget
- No custom protocol to build or maintain
- Standard, well-understood, battle-tested

**Why not a custom UDP sync protocol:**
- NTP already uses UDP
- NTP already handles round-trip estimation, jitter filtering, clock discipline
- A custom protocol would need to reimplement all of this to be better
- YAGNI — if NTP proves insufficient later, a precision layer can be added

### Implementation Details

**Existing work:** Mickey's fork of arduino-libraries/NTPClient ([mickeyil/NTPClient](https://github.com/mickeyil/NTPClient)) adds millisecond extraction from the NTP fractional seconds field. This is already used in elements v1 via the `SyncedTime` wrapper class.

**NTP sync frequency during playback:**
- ESP32 crystal oscillator drift: ~20-40 ppm (20-40 µs/sec)
- Over 5 minutes: 6-12ms drift — right at the edge of perceptible
- Recommendation: re-sync every 10-30 seconds during playback
- Note: v1 has a `// FIXME: accuracy issues bug when this is enabled` comment on periodic sync — this must be investigated and fixed in v2

**Inter-sync interpolation:**
Between NTP updates, the device interpolates time using `millis()`. The current `getEpochTime()` method in the NTPClient fork loses sub-second precision in this interpolation (integer division). v2 must ensure the `SyncedTime` wrapper returns proper `double` epoch time with ms fractions at all times, not just immediately after an NTP update.

**Non-blocking sync:**
The current `forceUpdate()` blocks for up to 1 second (polling with `delay(10)` in a loop). For v2, this should be made non-blocking so the render loop isn't stalled during NTP sync. Fire the NTP request, continue rendering, process the response when it arrives.

### Audio Playback Latency Compensation

The base station must account for its own audio pipeline latency. When the base station decides "beat hits speaker at wall-clock time T":

```
T_speaker = T_write_to_buffer + (buffered_frames / sample_rate)
```

Modern audio APIs (ALSA `snd_pcm_delay()`, PipeWire latency queries) provide this information. The base station uses `T_speaker` (not `T_write`) as the reference time in the animation program.

Previous experimentation: Mickey's `wavplayer` project demonstrated precise control of Linux audio APIs with verified sync between audio output and visual events (confirmed via slow-motion video capture).

### Drift Correction Strategy

If higher precision is needed in the future, the recommended approach is clock discipline (PLL-style):

1. Base station sends periodic sync pulses (could be NTP, could be custom UDP)
2. ESP32 measures offset between its clock and the base station's
3. Instead of jumping the clock (which causes visible glitches), slightly adjust the playback rate (±0.1-0.5%) to converge over a few seconds
4. This is how professional lighting systems handle it

This is explicitly **not planned for v2 initial implementation** — NTP should be sufficient. Documented here for future reference.

---

## Animation System

### Design Philosophy

- **Primitives are simple.** A small set of built-in animation functions (5-8), each parameterized.
- **Composition creates complexity.** Layering, blending, and repetition of simple primitives produces visually rich results.
- **Programs are compact.** ESP32 has limited RAM (~320KB). A full song's animation timeline must fit comfortably. Target: <20KB for a 5-minute track.
- **Rendering is local.** The ESP32 renders everything from its internal buffer. No streaming of pixel data over the network.

### Candidate Primitives

To be refined during implementation, but initial candidates:

| Primitive | Description |
|-----------|-------------|
| **fill** | Solid color fill (entire strip or range), with optional fade in/out |
| **gradient** | Linear gradient between two colors, mapped to strip position |
| **sweep** | Color/brightness moves along the strip at a given speed |
| **pulse** | Brightness oscillation (sine wave), parameterized by frequency and amplitude |
| **sparkle** | Random pixels flash at a configurable density and decay rate |
| **wave** | Sine-based color/brightness propagation along the strip |

Each primitive operates on an internal pixel buffer. Composition is achieved by layering primitives with alpha blending.

### Bytecode VM

The animation system is a lightweight bytecode interpreter — think of it as a tiny VM for LEDs. The base station compiles animation descriptions into bytecode; the ESP32 just executes it.

**Key bytecode concepts:**
- **Opcodes** reference built-in primitive functions with parameters
- **Control flow** supports repetition ("repeat N times", "loop for duration D")
- **Timing** is relative — offsets from a start anchor, not absolute timestamps
- **Composition** via render layers with blend modes (replace, additive, alpha)

**Encoding goals:**
- Compact: each opcode + params should be 4-20 bytes
- No dynamic memory allocation during playback
- No string parsing — pure binary, pre-compiled by the base station
- Parseable in a single linear pass (no backtracking)

**Detailed bytecode specification is TBD** — this is the main design task before implementation begins.

### Open Questions (Animation System)

1. **Stack-based VM vs. flat opcode list?** Stack-based is more flexible but more complex. A flat timeline with nested repeat blocks may be sufficient.
2. **How many simultaneous layers?** More layers = more RAM for pixel buffers. 2-3 layers is probably the sweet spot for ESP32.
3. **Color space:** v1 used HSV internally with RGB output. HSV makes interpolation prettier (hue rotation). Keep this?
4. **Spatial mapping:** v1 supported index remapping (logical pixel → physical strip position). Needed for non-linear strip shapes. Keep and extend?

---

## Comparison with v1

### What to keep from v1 (master branch)

- **PixelArray / Strip abstraction** — clean separation of logical pixels from physical strip layout. Index remapping is useful.
- **HSV color space** with gamma correction — proper LED color handling.
- **PC simulation path** — `#ifdef DEBUG_HELPERS` / `#ifdef ARDUINO` guards enabling desktop build and debug. Essential for development.
- **Time representation** — `double` epoch time with ms fractions (seconds.milliseconds format).
- **NTPClient fork** — millisecond-precision NTP sync.

### What to change

| v1 | v2 |
|----|-----|
| ESP8266 | ESP32 |
| Single channel, single animation type | Multi-layer, multiple primitives |
| C++ subclass per animation (compile-time) | Bytecode VM (runtime) |
| Binary packed struct wire format | Still binary, but versioned and structured as bytecode |
| All config via MQTT at runtime, lost on reboot | Stored presets on flash + MQTT overrides |
| `handlers_t` god struct passed everywhere | Cleaner dependency injection (TBD) |
| Error handling via `const char **errstr` | TBD — consider error codes |
| Blocking NTP sync | Non-blocking NTP sync |
| Mixed memory management (SlotsMM + new/malloc) | Consistent strategy (TBD — arena allocator?) |

### What to learn from the abandoned `new_animation_engine` branch

The branch introduced good ideas that were never completed:
- **Renderable** interface — generic render contract. Worth keeping.
- **AnimationSequence** — composable timeline of renderables. The concept is sound but needs the bytecode VM underneath.
- **`execute(code, progress)`** — the right API shape for the VM. Was never implemented.
- **Deletion of Channel class** — correct instinct. The channel abstraction from v1 was over-engineered for one animation type.

The branch stalled because the bytecode format was never defined. That's the critical design task.

---

## Hardware

### Current Setup
- **MCU:** ESP32 DevKit (ESP32-D0WDQ6, dual core 240MHz, 320KB RAM, 4MB flash)
- **LED:** WS2812B strip, connected to GPIO 13
- **USB:** /dev/ttyUSB0
- **Build system:** PlatformIO (verified working, Feb 2026)

### LED Strip

Physical strip details (length, shape, placement) are TBD. The software should be parameterized for arbitrary strip lengths. Current test setup: 1 LED on pin 13. Previous v1 setup: 50 LEDs.

---

## Build & Development

### PlatformIO

v2 uses PlatformIO (replacing Arduino CLI from v1). Verified working with:
- Platform: espressif32
- Board: esp32dev
- Framework: Arduino
- FastLED library

### PC Simulator

Maintaining the ability to build and test animation logic on desktop (Linux) without hardware. v1 had this via conditional compilation. v2 should preserve and improve it — especially important for developing and testing the bytecode VM.

---

## Project Phases (Proposed)

### Phase 1: Foundation
- Port to ESP32 + PlatformIO
- Establish project structure
- Define bytecode format specification
- Implement bytecode VM with 2-3 primitives
- PC simulator for VM testing

### Phase 2: Ambient Mode
- Implement remaining primitives
- Composition / layering
- Store programs on flash (LittleFS)
- MQTT interface for program upload and selection
- WiFi + NTP setup

### Phase 3: Music Sync
- Base station tooling (beat analysis, compiler)
- Audio playback with latency compensation
- MQTT protocol for program upload + start trigger
- End-to-end sync testing
- Multi-device support

---

## References

- Mickey's NTPClient fork: https://github.com/mickeyil/NTPClient
- Mickey's wavplayer experiment: audio latency testing with low-level Linux APIs
- elements v1 (master branch): working distance-sensor-triggered fill animation
- elements `new_animation_engine` branch: abandoned bytecode VM attempt (see `drafts/new_animation_engine_notes.md`)
