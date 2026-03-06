# Elements — Design Document

> **Status: Mixed.** Core pipeline (compiler, decoder, engine, compositor) is implemented. Hardware setup, build system, and project phases are current. Transport and time sync sections reflect the planned direction — see `playback_device.md` for details.

## Overview

Elements is an LED animation engine. The target hardware is ESP32. The device drives a WS2812B (NeoPixel) LED strip and operates in two modes:

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

TCP for commands (LOAD, START, debug) and clock sync results. UDP for clock sync probes (latency-sensitive RTT measurement) and streaming (RGB frames, telemetry). See `docs/playback_device.md` for the full protocol, packet formats, and device-side implementation.

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

The controller performs a lightweight SYNC_REQ/SYNC_RESP exchange with each ESP over the existing UDP command channel. ESPs are passive responders (timestamp and echo); all filtering, quality tracking, and correction logic lives on the controller.

**Why not NTP:** A generic NTP client on ESP is fragile — `forceUpdate()` blocks for up to 1s, `getEpochTime()` loses sub-second precision via integer division, and each ESP must manage its own NTP state. The custom protocol is simpler on the ESP side (~15 lines), non-blocking by design, and gives the controller full visibility into sync quality per device.

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

Detailed design documents:

**→ [abstractions.md](abstractions.md)** — Core abstractions: Strip, Layer, Animation, Program, Engine, Compositor, memory management

**→ [engine.md](engine.md)** — Engine runtime: tick loop, cursor design, factory, source layer dependencies

**→ [decoder.md](decoder.md)** — C++ decoder: blob parsing, struct layout, free_program

**→ [compiler.md](compiler.md)** — Python compiler pipeline: parser, time resolution, layer inference, buffer packing, blob emission

**→ [playback_device.md](playback_device.md)** — PlaybackDevice hierarchy, custom clock sync protocol, simulator debug extensions *(planned)*

**→ [draft_simulator_proposal.md](draft_simulator_proposal.md)** — Simulator: pybind11 + Flask + browser visualization *(early draft)*

**→ [dsl_example.py](dsl_example.py)** — DSL example: wave+shift+sparks test animation

---

## Hardware

### Current Setup
- **MCU:** ESP32 DevKit (ESP32-D0WDQ6, dual core 240MHz, 320KB RAM, 4MB flash)
- **LED:** WS2812B strip, connected to GPIO 13
- **USB:** /dev/ttyUSB0
- **Build system:** PlatformIO (verified working, Feb 2026)

### LED Strip

Physical strip details (length, shape, placement) are TBD. The software is parameterized for arbitrary strip lengths. Current test setup: 1 LED on pin 13.

---

## Build & Development

### PlatformIO

Uses PlatformIO. Verified working with:
- Platform: espressif32
- Board: esp32dev
- Framework: Arduino
- FastLED library

### PC Simulator

Build and test animation logic on desktop (Linux) without hardware via conditional compilation. Important for developing and testing the animation engine.

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

- Mickey's wavplayer experiment: audio latency testing with low-level Linux APIs
