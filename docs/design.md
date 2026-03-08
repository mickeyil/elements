# Elements — Design Document

> **Status: Mixed.** Core pipeline (compiler, decoder, engine, compositor, PlaybackDevice base class, ESPSimulated) and SimController (in-process sim-only controller) are implemented. Hardware setup, build system, and project phases are current. Transport and web app are designed but not yet implemented — see `transport.md` and `controller.md`.

## Overview

Elements is an LED animation engine. The target hardware is ESP32. The device drives a WS2812B (NeoPixel) LED strip and operates in two modes:

1. **Ambient mode** — standalone playback of pre-programmed, chill background animations
2. **Music sync mode** — precise beat-synced animations driven by a base station that performs audio analysis and compiles animation timelines

The core design principle: **tight timing sync over complex rendering**. Simple animations that land exactly on the beat are more impressive than complex effects with sloppy timing.

---

## System Architecture

```
┌──────────────────────────────────────────────────┐
│                   Base Station                    │
│            (NUC / Raspberry Pi / Linux box)       │
│                                                   │
│  ┌──────────────────┐     ┌───────────────────┐  │
│  │    Controller     │     │     Web App       │  │
│  │  (compiles, routes│<--->│  (serves browser, │  │
│  │   blobs, syncs,   │     │   relays commands │  │
│  │   manages sessions│     │   and frames)     │  │
│  │   + playback)     │     └────────┬──────────┘  │
│  └────────┬──────────┘              │ WebSocket   │
│           │                         │             │
│     TCP + UDP                    Browser          │
│     (device protocol)            (canvas, UI)     │
│           │                                       │
└───────────┼───────────────────────────────────────┘
            │ WiFi (same LAN)
     ┌──────┴──────┐
     │   Devices   │
     │  (ESP32 or  │
     │  Simulated) │
     └─────────────┘
```

The controller is the long-running authority. It compiles DSL programs, routes per-strip blobs to devices via a static config, manages clock sync and playback sessions, and exposes a control/event API. The web app is a separate process that relays between the controller and the browser. See `controller.md` for the full architecture, config format, and session identity model.

### Network Assumptions

- Base station and ESP32 share the same WiFi network
- No firewall or routing restrictions between them
- Base station runs controller + web app as separate processes
- Static config maps strip names to device IPs
- Multiple ESP32 devices synced to the same controller

---

## Base Station Protocol

Communication between the base station and devices is minimal by design. The controller does all the heavy lifting (analysis, compilation, routing); the device is a dumb playback engine.

### Messages

| Message | Direction | Description |
|---------|-----------|-------------|
| **LOAD(blob, gen)** | base → device | Upload a compiled blob + generation counter. Clears all device state. Device decodes and enters LOADED. |
| **ACK** | device → base | Confirms program was received and decoded successfully. |
| **START(t0)** | base → device | Begin playback at shared absolute timestamp `t0` (int64_t µs). |
| **JUMP(t0, t_rel, gen)** | base → device | Seek to a reset-safe time. Resets engine, sets shared `t0` and new `gen`. |
| **PAUSE** | base → device | Stop advancing. Keep displaying last frame. Report paused `t_rel`. |
| **RESUME(t0)** | base → device | Resume from paused state with shared `t0`. No engine reset — state preserved. |
| **STOP** | base → device | Clear output to black, reset engine to t=0, transition to LOADED. Program stays loaded. |

See `docs/transport.md` for wire formats and sync protocol, `docs/controller.md` for gen filtering, safe intervals, and debug commands, and `docs/playback_device.md` for the device state machine.

### Playback Flow

**Song playback:**
```
Controller → Device: LOAD(blob, gen=1)
Device → Controller: ACK
Controller → Device: START(t0)
Controller: begins audio playback timed to T0
Device: executes from T0
  ... song plays ...
Song ends. Device goes dark.
Controller → Device: LOAD(next blob, gen=2)
```

**Seeking (production):**
```
Controller → Device: JUMP(t0, t_rel=28.0, gen=3)
Controller → Audio: seek_and_start_at(28.0, t0)
Device: reset(), resume from t_rel with shared t0
Audio: repositions to 28.0, starts at t0
```

**Stopping playback (program stays loaded):**
```
Controller → Device: STOP
Device: clears to black, resets to t=0, stays LOADED
Controller → Device: START(t0)   // can replay later without re-uploading
```

**Replacing program mid-song:**
```
Controller → Device: LOAD(ambient blob, gen=N)
```
LOAD tears down the current program and replaces it — no STOP needed when switching programs.

**Default behavior:** When a program finishes and no new program is loaded, the device goes dark. This is the expected state between songs — the controller sends the next program when ready.

### Transport

TCP for commands (LOAD, START, JUMP, PAUSE, RESUME, STOP, debug) and clock sync results. UDP for clock sync probes (latency-sensitive RTT measurement) and streaming (RGB frames, telemetry). See `docs/transport.md` for the full transport architecture.

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

**Full design and implementation details:** See `docs/transport.md`, section "Custom sync protocol".

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

**→ [compiler.md](compiler.md)** — Python compiler pipeline: parser, time resolution, layer inference, buffer packing, blob emission, safe interval analysis

**→ [playback_device.md](playback_device.md)** — PlaybackDevice base class: state machine, method contracts, ESPDevice/ESPSimulated subclass sketches *(base class implemented, subclasses planned)*

**→ [transport.md](transport.md)** — Device communication protocol: TCP commands, UDP sync probes, wire formats, custom clock sync *(design)*

**→ [controller.md](controller.md)** — Controller/web-app architecture: config, identity model, frame assembly, reset-safe intervals, protocols, end-to-end flows *(design)*

**→ [draft_simulator_proposal.md](draft_simulator_proposal.md)** — Simulator: pybind11 + Flask + browser visualization *(early draft, partially superseded by playback_device.md and controller.md)*

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
