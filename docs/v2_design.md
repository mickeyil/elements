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
│  ┌──────────────┐  ┌──────────────┐  │
│  │ Controller   │  │ Audio Engine │  │
│  │ (sync, LOAD, │  │ (ALSA/Pipe)  │  │
│  │  START, telem│  │              │  │
│  └──────┬───────┘  └──────┬───────┘  │
│         │                 │          │
│  ┌──────┴─────────────────┴───────┐  │
│  │ Beat Analyzer + Compiler      │  │
│  │ (offline analysis → bytecode) │  │
│  └───────────────┬───────────────┘  │
│                  │                  │
│              UDP │ (commands, sync, │
│                  │  blobs, telemetry│
└──────────────────┼──────────────────┘
                   │ WiFi (same LAN)
┌──────────────────┼──────────────────┐
│  ESP32 Device    │                  │
│                  │                  │
│  ┌───────────────┴───────────────┐  │
│  │ UDP command handler           │  │
│  │ (LOAD, START, SYNC_REQ)      │  │
│  └───────────────┬───────────────┘  │
│                  │                  │
│  ┌───────────────┴───────────────┐  │
│  │ Animation VM                  │  │
│  │ (bytecode interpreter)        │  │
│  │ renders to layer buffers      │  │
│  └───────────────┬───────────────┘  │
│                  │                  │
│  ┌───────────────┴───────────────┐  │
│  │ Compositor → FastLED → strip  │  │
│  └───────────────────────────────┘  │
└─────────────────────────────────────┘
```

### Network Assumptions

- Base station and ESP32 share the same WiFi network
- No firewall or routing restrictions between them
- Base station runs controller (UDP commands + custom clock sync)
- Future: potentially multiple ESP32 devices synced to the same base station

---

## Base Station Protocol

Communication between the base station and the ESP32 device is minimal by design. The base station does all the heavy lifting (analysis, compilation); the device is a dumb playback engine.

### Messages

| Message | Direction | Description |
|---------|-----------|-------------|
| **LOAD** | base → device | Upload a bytecode program. Implicitly clears all device state (active layers, animations, buffers). The device is in a fresh state ready to execute. |
| **ACK** | device → base | Confirms program was received and loaded successfully. |
| **START** | base → device | Begin executing the loaded program at absolute timestamp T0 (int64_t µs, controller monotonic reference). |

### Playback Flow

**Song playback:**
```
Base station → ESP32: LOAD (song bytecode, ~3KB)
ESP32 → Base station: ACK
Base station → ESP32: START at T0
Base station: begins audio playback timed to T0 (accounting for audio pipeline latency)
ESP32: executes bytecode from T0
  ... song plays ...
Song ends. Program finishes. Device goes dark.
Base station → ESP32: LOAD (ambient bytecode or next song)
```

**Stopping mid-song:**
```
Base station → ESP32: LOAD (ambient bytecode)
```
LOAD clears everything — the song's animations stop immediately, ambient program takes over. No explicit stop command needed.

**Default behavior:** When a program finishes and no new program is loaded, the device goes dark. This is the expected state between songs — the base station sends the next program when ready.

### Transport

UDP over the shared WiFi network. Programs are sent as binary payloads. Transport protocol details (raw UDP vs MQTT) TBD — see open issues in `docs/playback_device.md`.

---

## Operating Modes

### Ambient Mode

- A bytecode program like any other, but designed to loop indefinitely
- Can be stored on device flash (LittleFS) for power-on default
- Can be replaced anytime via LOAD
- Think: slow color waves, gentle breathing, gradient sweeps

### Music Sync Mode

1. **Pre-analysis:** Before playback, the base station analyzes the audio track offline — beat detection, energy analysis, frequency band decomposition
2. **Compilation:** Results are compiled into a bytecode program (cached for reuse)
3. **Upload:** LOAD bytecode to device, wait for ACK
4. **Trigger:** START with absolute timestamp T0
5. **Playback:** Device executes bytecode against its controller-synced clock

---

## Time Synchronization

### Requirements

- Audio-visual sync tolerance: **±10ms** (human perception threshold for beat-sync is ~20ms; we target half that for margin)
- Must hold sync over the duration of a song (3-7 minutes)
- ESP32 crystal oscillator drift: ~20-40 ppm → 6-12ms over 5 minutes

### Solution: Custom controller-led sync protocol

Replaces the v1 NTP approach. The controller performs a lightweight SYNC_REQ/SYNC_RESP exchange with each ESP over the existing UDP command channel. ESPs are passive responders (timestamp and echo); all filtering, quality tracking, and correction logic lives on the controller.

**Why not NTP:** The v1 NTPClient approach was fragile — `forceUpdate()` blocks for up to 1s, `getEpochTime()` loses sub-second precision via integer division, and each ESP must manage its own NTP state. The custom protocol is simpler on the ESP side (~15 lines), non-blocking by design, and gives the controller full visibility into sync quality per device.

**Full design and implementation details:** See `docs/playback_device.md`, section "Custom sync protocol".

### Audio Playback Latency Compensation

The base station must account for its own audio pipeline latency. When the base station decides "beat hits speaker at wall-clock time T":

```
T_speaker = T_write_to_buffer + (buffered_frames / sample_rate)
```

Modern audio APIs (ALSA `snd_pcm_delay()`, PipeWire latency queries) provide this information. The base station uses `T_speaker` (not `T_write`) as the reference time in the animation program.

Previous experimentation: Mickey's `wavplayer` project demonstrated precise control of Linux audio APIs with verified sync between audio output and visual events (confirmed via slow-motion video capture).

---

## Rendering Abstractions & Animation System

Detailed design of all core abstractions — Strip, Layer, Animation, AnimationEvent, Program, Engine, Compositor, and memory management — is in the dedicated document:

**→ [abstractions.md](abstractions.md)**

This covers terminology, data structures, code snippets, worked examples, and design rationale.

The DSL and compiler design are in separate documents:

**→ [dsl_example.py](dsl_example.py)** — DSL example: wave+shift+sparks test animation

**→ [compiler.md](compiler.md)** — Compiler pipeline: parser, time resolution, layer inference, buffer packing, blob emission

**→ [decoder.md](decoder.md)** — C++ decoder: arena allocation, tagged union params, decode flow

---

## Comparison with v1

### What to keep from v1 (master branch)

- **PixelArray / Strip abstraction** — the core concept of logical-to-physical pixel mapping. Evolved into the Layer abstraction in v2 (which merges index mapping + color buffer).
- **HSV color space** with gamma correction — proper LED color handling. Extended with per-pixel alpha in v2.
- **PC simulation path** — `#ifdef DEBUG_HELPERS` / `#ifdef ARDUINO` guards enabling desktop build and debug. Essential for development.
- **Time representation** — `double` epoch time with ms fractions (seconds.milliseconds format).
- **NTPClient fork** — millisecond-precision NTP sync (superseded by custom sync protocol in v2, see `docs/playback_device.md`).
- **SlotsMM concept** — pre-allocated memory pool. Will be adapted for v2's dynamic layer allocation.

### What to change

| v1 | v2 |
|----|-----|
| ESP8266 | ESP32 |
| Single channel, single animation type | Multi-layer, multiple primitives, per-pixel alpha |
| C++ subclass per animation (compile-time) | Bytecode VM (runtime, primitives are built-in) |
| Binary packed struct wire format | Bytecode programs compiled by base station |
| Static channel setup via MQTT | All layers dynamic, created by bytecode |
| All config via MQTT at runtime, lost on reboot | Stored presets on flash + LOAD/START protocol |
| `handlers_t` god struct passed everywhere | Cleaner dependency injection (TBD) |
| Error handling via `const char **errstr` | TBD — consider error codes |
| Blocking NTP sync | Custom controller-led sync protocol (see `docs/playback_device.md`) |
| Mixed memory management (SlotsMM + new/malloc) | Pre-allocated memory pool for all runtime buffers |
| Blending: timeline trim (new animation cuts old) | Blending: per-pixel alpha compositing in RGB space |

### What to learn from the abandoned `new_animation_engine` branch

The branch introduced good ideas that were never completed:
- **Renderable** interface — generic render contract. The concept survives in v2's animation primitives.
- **AnimationSequence** — composable timeline of renderables. Replaced by bytecode control flow (REPEAT, timing offsets).
- **`execute(code, progress)`** — the right API shape for the VM. Was never implemented.
- **Deletion of Channel class** — correct instinct. Replaced by the simpler Layer model.

The branch stalled because the bytecode format was never defined. That remains the critical next design task.

---

## Hardware

### Current Setup
- **MCU:** ESP32 DevKit (ESP32-D0WDQ6, dual core 240MHz, 320KB RAM, 4MB flash)
- **LED:** WS2812B strip, connected to GPIO 13
- **USB:** /dev/ttyUSB0
- **Build system:** PlatformIO (verified working, Feb 2026)

### LED Strip

Physical strip details (length, shape, placement) are TBD. The software is parameterized for arbitrary strip lengths. Current test setup: 1 LED on pin 13. Previous v1 setup: 50 LEDs.

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
- Port to ESP32 + PlatformIO project structure
- Implement core abstractions: Strip, Layer, Compositor
- Define bytecode format specification
- Implement bytecode VM with 2-3 primitives
- PC simulator for VM testing
- Memory pool allocator

### Phase 2: Ambient Mode
- Implement remaining primitives
- Store programs on flash (LittleFS)
- UDP interface for LOAD/START/SYNC protocol
- WiFi + controller clock sync setup
- Power-on default program

### Phase 3: Music Sync
- Base station tooling (beat analysis, program compiler)
- Audio playback with latency compensation
- End-to-end sync testing
- Multi-device support

---

## References

- Mickey's NTPClient fork: https://github.com/mickeyil/NTPClient
- Mickey's wavplayer experiment: audio latency testing with low-level Linux APIs
- elements v1 (master branch): working distance-sensor-triggered fill animation
- elements `new_animation_engine` branch: abandoned bytecode VM attempt
