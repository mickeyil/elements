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
│  │ (programs, commands)          │  │
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
- Base station runs NTP server + MQTT broker
- Future: potentially multiple ESP32 devices synced to the same base station

---

## Base Station Protocol

Communication between the base station and the ESP32 device is minimal by design. The base station does all the heavy lifting (analysis, compilation); the device is a dumb playback engine.

### Messages

| Message | Direction | Description |
|---------|-----------|-------------|
| **LOAD** | base → device | Upload a bytecode program. Implicitly clears all device state (active layers, animations, buffers). The device is in a fresh state ready to execute. |
| **ACK** | device → base | Confirms program was received and loaded successfully. |
| **START** | base → device | Begin executing the loaded program at absolute NTP timestamp T0. |

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

MQTT over the shared WiFi network. Programs are sent as binary payloads. MQTT topic structure TBD but will follow the v1 pattern: `elements/<device_id>/...`

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
5. **Playback:** Device executes bytecode against its NTP-synced clock

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

### Drift Correction Strategy (Future)

If higher precision is needed in the future, the recommended approach is clock discipline (PLL-style):

1. Base station sends periodic sync pulses (could be NTP, could be custom UDP)
2. ESP32 measures offset between its clock and the base station's
3. Instead of jumping the clock (which causes visible glitches), slightly adjust the playback rate (±0.1-0.5%) to converge over a few seconds
4. This is how professional lighting systems handle it

This is explicitly **not planned for v2 initial implementation** — NTP should be sufficient. Documented here for future reference.

---

## Rendering Abstractions

The rendering pipeline has two data structures and a render loop: **Strip**, **Layer**, and **Compositor**.

These were designed bottom-up to support two key scenarios:

1. A continuous strip shaped into a physical form (e.g., a flower) where subsets (center, petals) are animated independently
2. Overlay effects (e.g., beat-synced white sparks) blended on top of a background animation

### Strip

The physical LED output buffer. A thin wrapper around FastLED's CRGB array (or a plain RGB buffer for the PC simulator).

- RGB or BGR byte order (determined by hardware wiring)
- Length = total number of physical LEDs
- One Strip instance per device
- The compositor blends directly into this buffer — there is no separate composite buffer

### Layer

A layer combines pixel addressing, color data, and compositing priority into a single concept. It is both "which physical pixels" and "what color/alpha values" in one object.

**Properties:**
- **Numeric ID** — assigned by bytecode
- **Index mapping** — array of `logical_index → physical_strip_index`, defines which physical LEDs this layer addresses
- **Length** — number of logical pixels in the layer
- **HSVA pixel buffer** — sized to the layer's length (not full strip length)
  - H, S, V: `float` — internal color representation in HSV space
  - A: `float` — alpha channel, range [0.0, 1.0]
- **Priority** — determines compositing order (lower = further back, higher = on top)

**Lifecycle:** Created and destroyed by bytecode instructions.
```
CREATE_LAYER id=0, indices=[0..49], priority=0     → background, all 50 LEDs
CREATE_LAYER id=1, indices=[4,11,21,25], priority=1 → spark overlay, 4 LEDs
DISCARD_LAYER id=1                                  → frees buffer + index mapping
```

**All layers are dynamic.** There is no distinction between "persistent" and "ephemeral" layers at the type level. A layer that lives for the entire program is simply one that's never discarded. A layer that lives for 468ms gets created, used, and discarded by the bytecode. The lifecycle is entirely controlled by the program.

**Layers may overlap in physical indices.** A background layer on [0..49] and a spark layer on [4,11,21,25] both address physical LEDs 4, 11, 21, and 25. The compositor resolves this via priority ordering and alpha blending.

**Why merged (not separate Pixel Group + Layer):**
In every scenario we evaluated, pixel groups and layers had a 1:1 relationship — created together, destroyed together. Keeping them as separate concepts added an abstraction and extra bytecode instructions for no practical benefit. If two animations need the same pixel set, create two layers with the same indices — the duplicated index array is a few bytes.

**Why HSVA:**
- HSV is natural for LED animation — hue rotation produces rainbows, saturation and value control is intuitive
- Animations author in HSV. Conversion to RGB happens once at composite time.
- **Alpha is per-pixel**, not per-layer. This enables effects where individual pixels within a layer have different opacity — e.g., cascading sparks where each pixel fires and fades independently.

**Why per-pixel alpha:**
A per-layer blend coefficient forces all pixels to blend at the same ratio. This prevents effects like cascading sparks (pixel 0 at full brightness while pixel 3 is half-faded). Per-pixel alpha costs one extra float per pixel but enables significantly richer animations with negligible performance impact.

**Buffer sizing:**
Layer buffers are sized to the layer's pixel count, not the full strip length. A 4-pixel spark layer = 4 × 16 bytes = 64 bytes. A 50-pixel background layer = 800 bytes. Memory is only allocated for pixels that are actually being animated.

**Animations see an isolated world.** An animation targeting a 4-pixel layer sees a buffer of pixels [0, 1, 2, 3]. It has no knowledge that these map to physical LEDs 4, 11, 21, and 25. The index mapping is the layer's concern, resolved at composite time.

### Compositor

The compositor runs every render frame (~100Hz target). It alpha-blends all active layers into the Strip.

**There is only one blending mode: alpha.** The background layer simply sets A=1.0 on all its pixels, which fully writes its colors — no special "replace" mode needed. Overlay layers use intermediate alpha values for smooth blending. When alpha reaches 0.0, the pixel is fully transparent and the layer below shows through.

**Pipeline:**

```
1. Clear Strip to black (memset CRGB array to 0)

2. For each active layer, ordered by priority (lowest first):
   a. Animation renders into the layer's HSVA buffer
   b. For each pixel i in the layer:
      physical_idx = layer.index_map[i]
      pixel_rgb = hsv_to_rgb(layer.buffer[i])
      alpha = layer.buffer[i].a
      strip[physical_idx] = lerp(strip[physical_idx], pixel_rgb, alpha)

3. FastLED.show()
```

**Why the compositor blends directly into the Strip (no intermediate buffer):**
The first layer (background, A=1.0) writes `lerp(black, color, 1.0) = color`. Subsequent layers blend on top. Pixels not covered by any layer stay black. This is correct in all cases and eliminates a separate full-strip composite buffer.

**Blending happens in RGB space**, not HSV. This is deliberate:
- HSV blending breaks down when saturation values differ. Blending white (S=0) with deep blue (S=255) in HSV produces unpredictable results because the hue component is meaningless at S=0 but still participates in interpolation.
- RGB blending of white over deep blue correctly produces pale blue — which matches visual expectation.
- Cost: one `hsv_to_rgb` conversion per active pixel per layer per frame. At 50 pixels, 3 layers, 100Hz = ~15,000 conversions/sec. Negligible on ESP32 at 240MHz.

### Worked Example: Flower with Beat-Synced Sparks

**Physical setup:** 50 LEDs shaped as a flower. Center = LEDs 0-9, petals = LEDs 10-49.

**Desired effect:** Slow hue wave across entire flower (background). On every 4th beat, a few pixels flash white and fade out over ~1 beat.

**Bytecode execution (128 BPM, 1 beat = 468ms):**

```
Program start:
  CREATE_LAYER id=0, indices=[0..49], priority=0
  START_ANIMATION layer=0, type=HUE_WAVE, params={...}    → runs continuously

At t=12.500s (beat-aligned):
  CREATE_LAYER id=1, indices=[4, 11, 21, 25], priority=1
  START_ANIMATION layer=1, type=SPARK, duration=468ms, params={color=white}
    → Animation writes HSVA per pixel:
      t+0ms:   H=0, S=0, V=255, A=1.0  (full white, fully opaque)
      t+234ms: H=0, S=0, V=255, A=0.5  (white, half blended with background)
      t+468ms: H=0, S=0, V=255, A=0.0  (fully transparent → background shows through)

At t=12.968s (animation ends):
  DISCARD_LAYER id=1                                      → frees buffer + indices

At t=14.375s (next spark):
  CREATE_LAYER id=2, indices=[7, 19, 33, 42, 48], priority=1
  START_ANIMATION layer=2, type=SPARK, duration=468ms, params={color=white}
  ...

At t=14.843s:
  DISCARD_LAYER id=2
```

**What the viewer sees:** A chill, slowly changing colorful flower. Every ~2 seconds, a handful of pixels flash bright white and smoothly fade back into the background animation. The flash lands exactly on the musical beat.

---

## Animation System

### Design Philosophy

- **Primitives are simple.** A small set of built-in animation functions (5-8), each parameterized.
- **Composition creates complexity.** Layering and repetition of simple primitives produces visually rich results.
- **Programs are compact.** ESP32 has limited RAM (~320KB). A full song's animation timeline must fit comfortably. Target: <20KB for a 5-minute track.
- **Rendering is local.** The ESP32 renders everything from its internal buffer. No streaming of pixel data over the network.

### Candidate Primitives

To be refined during implementation, but initial candidates:

| Primitive | Description |
|-----------|-------------|
| **fill** | Solid color fill, with optional fade in/out |
| **gradient** | Linear gradient between two colors, mapped to pixel position |
| **sweep** | Band of color moving along the layer's pixels |
| **pulse** | Brightness oscillation (sine wave), parameterized by frequency and amplitude |
| **sparkle** | Pixels flash at configurable density with per-pixel fade. Uses seeded PRNG for deterministic results (required for multi-device sync). |
| **wave** | Sine-based color/brightness propagation along the layer's pixels |

Each primitive:
- Receives a layer's HSVA buffer (pixel-group-sized)
- Sees an isolated 0..N-1 pixel world (no knowledge of physical layout)
- Controls per-pixel alpha for blending
- Receives `t_rel` (time relative to animation start) and its parameters

### Bytecode VM

The animation system is a lightweight bytecode interpreter. The base station compiles animation descriptions into bytecode; the ESP32 executes it.

**Key concepts:**
- **Opcodes** reference built-in primitives, layer management, and control flow
- **Control flow** supports repetition (`REPEAT N times`, `REPEAT for duration D`)
- **Timing** is relative — offsets from program start or enclosing repeat block
- **Layer management** is explicit in the bytecode (CREATE_LAYER, DISCARD_LAYER)

**Encoding goals:**
- Compact: each instruction should be 4-20 bytes
- Pre-allocated memory pool for runtime buffers (no heap alloc during playback)
- Pure binary, pre-compiled by the base station
- Parseable in a single linear pass

**Size estimate:** A 5-minute house track at 128 BPM with heavy use of repeat blocks: **2-5KB** for the full program. Well within ESP32 limits.

**Detailed bytecode specification is TBD** — to be designed next.

### Open Questions (Animation System)

1. **Nested repeats.** Repeats inside repeats would massively improve compression for music (repeat 4-bar pattern 8 times, within each bar repeat beat pattern 4 times). Adds interpreter complexity. Recommend: yes, with a max nesting depth of 3-4.
2. **Parameter interpolation over time.** Should primitives support changing parameters mid-animation (e.g., a sweep that accelerates)? Start without this; add as a modifier opcode later if needed.
3. **Seeded PRNG for sparkle.** Multi-device sync requires deterministic randomness. Seed should be per-instruction so the same program produces identical results on every device.
4. **Memory pool sizing.** Need to define max concurrent layers and pre-allocate accordingly. Proposed budget: 4KB for layer buffers (~250 float-HSVA pixels, or ~20 layers of ~12 pixels each).

---

## Comparison with v1

### What to keep from v1 (master branch)

- **PixelArray / Strip abstraction** — the core concept of logical-to-physical pixel mapping. Evolved into the Layer abstraction in v2 (which merges index mapping + color buffer).
- **HSV color space** with gamma correction — proper LED color handling. Extended with per-pixel alpha in v2.
- **PC simulation path** — `#ifdef DEBUG_HELPERS` / `#ifdef ARDUINO` guards enabling desktop build and debug. Essential for development.
- **Time representation** — `double` epoch time with ms fractions (seconds.milliseconds format).
- **NTPClient fork** — millisecond-precision NTP sync.
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
| Blocking NTP sync | Non-blocking NTP sync |
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
- MQTT interface for LOAD/START protocol
- WiFi + NTP setup
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
