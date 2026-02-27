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
│  │ renders to pixel buffers      │  │
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
| **LOAD** | base → device | Upload a bytecode program. Implicitly clears all device state (active groups, layers, animations, buffers). The device is in a fresh state ready to execute. |
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

The rendering pipeline has four core concepts: **Strip**, **Pixel Group**, **Layer**, and **Compositor**. These were designed bottom-up to support two key scenarios:

1. A continuous strip shaped into a physical form (e.g., a flower) where named subsets (center, petals) are animated independently
2. Overlay effects (e.g., beat-synced white sparks) blended on top of a background animation

### Strip

The physical LED output buffer.

- `uint8_t[]` in RGB or BGR byte order (determined by hardware wiring)
- Length = total number of physical LEDs
- **Write-only target** for the final composited result
- Knows its color order and length. Nothing else.
- One Strip instance per device.

### Pixel Group

A logical grouping of physical LEDs, identified by an ID. Provides the mapping between logical pixel indices (0, 1, 2, ...) and physical LED positions on the strip.

**All pixel groups are dynamic.** There is no distinction between "static" and "ephemeral" groups at the type level. A group that persists for the entire program is simply one that's never discarded — its lifecycle is controlled entirely by the bytecode.

**Properties:**
- Numeric ID (assigned by bytecode)
- Index mapping array: `logical_index → physical_strip_index`
- Length (number of logical pixels in the group)

**Lifecycle:** Created and destroyed by bytecode instructions.
- `CREATE_GROUP id, indices[...]` — allocates the index mapping
- `DISCARD_GROUP id` — frees the index mapping and any associated layers

**Examples:**
```
CREATE_GROUP id=0, indices=[0..49]         → "all 50 LEDs"
CREATE_GROUP id=1, indices=[0..9]          → "flower center"
CREATE_GROUP id=2, indices=[4,11,21,25]    → "spark target pixels"
```

Pixel groups may overlap in their physical indices. This is by design — a background animation on "all LEDs" and a spark overlay on a subset of those LEDs will target overlapping physical pixels. The compositor resolves this via layer ordering.

**Design rationale — why all dynamic:**
- One concept instead of two (static vs ephemeral). Simpler mental model, simpler code.
- The device firmware doesn't need a configuration phase. Everything comes from the bytecode.
- The base station has full control over group lifecycle.
- A "static" group is just a dynamic group that's never discarded.

### Layer

A render surface attached to a pixel group. This is where animations write their output.

**Properties:**
- Associated pixel group (by ID)
- **HSVA pixel buffer** — sized to the pixel group's length (not full strip length)
  - H, S, V: `float` — internal color representation in HSV space
  - A: `float` — alpha channel, range [0.0, 1.0]. Controls blending with layers below.
- Blend mode: `REPLACE`, `ALPHA`, `ADDITIVE` (extensible)
- Priority: determines compositing order (lower = rendered first = further back)

**Why HSVA:**
- HSV is natural for LED animation — hue rotation produces rainbows, saturation/value control is intuitive.
- Animations author in HSV. Conversion to RGB happens once, at composite time.
- **Alpha is per-pixel**, not per-layer. This enables effects where individual pixels within a group have different opacity — e.g., a spark animation where each pixel fades independently, or cascading flashes where pixels fire in sequence.

**Why per-pixel alpha (not per-layer coefficient):**
A per-layer blend coefficient forces all pixels in the group to blend at the same ratio. This prevents effects like cascading sparks (pixel 0 at full brightness while pixel 3 is half-faded). Per-pixel alpha costs one float per pixel of extra memory but enables significantly richer animations with negligible performance impact.

**Buffer sizing:**
Layer buffers are pixel-group-sized, not full-strip-sized. A spark layer targeting 4 pixels allocates a 4-pixel HSVA buffer (64 bytes with float HSVA), not a 50-pixel buffer. This is efficient — most groups are small and most layers are short-lived.

Memory estimate: `pixels × 16 bytes` (4 floats: H, S, V, A). A 50-pixel background layer = 800 bytes. Four simultaneous 4-pixel spark layers = 256 bytes. Well within ESP32 budget.

**Lifecycle:** Created and destroyed alongside their pixel group, or by explicit bytecode instructions if multiple layers per group are needed.

### Compositor

The compositor runs every render frame (~100Hz target). It blends all active layers into the strip output buffer.

**Pipeline:**

```
1. Clear composite buffer (RGB, full strip length)

2. For each active layer, ordered by priority (lowest first):
   a. Animation renders into the layer's HSVA buffer
   b. For each pixel i in the layer's pixel group:
      physical_idx = pixel_group.index_map[i]
      pixel_rgb = hsv_to_rgb(layer.buffer[i])
      alpha = layer.buffer[i].a
      
      if blend_mode == REPLACE:
        composite[physical_idx] = pixel_rgb
      elif blend_mode == ALPHA:
        composite[physical_idx] = lerp(composite[physical_idx], pixel_rgb, alpha)
      elif blend_mode == ADDITIVE:
        composite[physical_idx] = clamp(composite[physical_idx] + pixel_rgb * alpha)

3. Copy composite buffer → Strip (applying color order)
4. FastLED.show()
```

**Blending happens in RGB space**, not HSV. This is a deliberate choice:

- HSV blending breaks down when saturation values differ greatly. Blending white (S=0) with deep blue (S=255) in HSV produces an unpredictable hue because the H component is meaningless at S=0 but still participates in interpolation.
- RGB blending of white over deep blue correctly produces pale blue — which is what the eye expects.
- Cost: one `hsv_to_rgb` conversion per active pixel per layer per frame. At 50 pixels, 3 layers, 100Hz = ~15,000 conversions/sec. Each is a few dozen integer ops. ESP32 at 240MHz handles this easily.

**Composite buffer:** RGB, full strip length. 50 LEDs × 3 bytes = 150 bytes. This is the one full-strip-sized allocation. Individual layer buffers remain pixel-group-sized.

### Worked Example: Flower with Beat-Synced Sparks

**Physical setup:** 50 LEDs shaped as a flower. Center = LEDs 0-9, petals = LEDs 10-49.

**Desired effect:** Slow hue wave across entire flower (background). On every 4th beat, a few random pixels flash white and fade out over ~1 beat.

**Bytecode execution (128 BPM, 1 beat = 468ms):**

```
Program start:
  CREATE_GROUP id=0, indices=[0..49]                    → all 50 LEDs
  CREATE_LAYER group=0, priority=0, blend=REPLACE
  START_ANIMATION layer=0, type=HUE_WAVE, params={...}  → runs continuously

At t=12.500s (beat-aligned):
  CREATE_GROUP id=1, indices=[4, 11, 21, 25]
  CREATE_LAYER group=1, priority=1, blend=ALPHA
  START_ANIMATION layer=1, type=SPARK, duration=468ms, params={color=white}
    → Animation writes HSVA per pixel:
      t+0ms:   H=0, S=0, V=255, A=1.0  (full white, fully opaque)
      t+234ms: H=0, S=0, V=255, A=0.5  (white, half blended with background)
      t+468ms: H=0, S=0, V=255, A=0.0  (fully transparent → background shows through)

At t=12.968s (animation ends):
  DISCARD_GROUP id=1                                    → frees layer + buffer + indices

At t=14.375s (next spark):
  CREATE_GROUP id=2, indices=[7, 19, 33, 42, 48]
  CREATE_LAYER group=2, priority=1, blend=ALPHA
  START_ANIMATION layer=2, type=SPARK, duration=468ms, params={color=white}
  ...

At t=14.843s:
  DISCARD_GROUP id=2
```

**What the viewer sees:** A chill, slowly changing colorful flower. Every ~2 seconds, a handful of pixels flash bright white and smoothly fade back into the background animation. The flash lands exactly on the musical beat.

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
| **fill** | Solid color fill, with optional fade in/out |
| **gradient** | Linear gradient between two colors, mapped to pixel position |
| **sweep** | Band of color moving along the pixel group |
| **pulse** | Brightness oscillation (sine wave), parameterized by frequency and amplitude |
| **sparkle** | Pixels flash at configurable density with per-pixel fade. Uses seeded PRNG for deterministic results (required for multi-device sync). |
| **wave** | Sine-based color/brightness propagation along the pixel group |

Each primitive:
- Receives a pixel-group-sized HSVA buffer
- Sees an isolated 0..N-1 pixel world (no knowledge of physical layout)
- Controls per-pixel alpha for blending
- Receives `t_rel` (time relative to animation start) and its parameters

### Bytecode VM

The animation system is a lightweight bytecode interpreter. The base station compiles animation descriptions into bytecode; the ESP32 executes it.

**Key concepts:**
- **Opcodes** reference built-in primitives, group management, and control flow
- **Control flow** supports repetition (`REPEAT N times`, `REPEAT for duration D`)
- **Timing** is relative — offsets from program start or enclosing repeat block
- **Group/layer management** is explicit in the bytecode (CREATE, DISCARD)

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
4. **Memory pool sizing.** Need to define max concurrent groups/layers and pre-allocate accordingly. Proposed budget: 4KB for ephemeral pixel group buffers (~250 float-HSVA pixels, or ~20 groups of ~12 pixels each).

---

## Comparison with v1

### What to keep from v1 (master branch)

- **PixelArray / Strip abstraction** — the core concept of logical-to-physical pixel mapping. Evolved into Pixel Group + Layer in v2.
- **HSV color space** with gamma correction — proper LED color handling. Extended with per-pixel alpha in v2.
- **PC simulation path** — `#ifdef DEBUG_HELPERS` / `#ifdef ARDUINO` guards enabling desktop build and debug. Essential for development.
- **Time representation** — `double` epoch time with ms fractions (seconds.milliseconds format).
- **NTPClient fork** — millisecond-precision NTP sync.
- **SlotsMM concept** — pre-allocated memory pool. Will be adapted for v2's dynamic pixel group/layer allocation.

### What to change

| v1 | v2 |
|----|-----|
| ESP8266 | ESP32 |
| Single channel, single animation type | Multi-layer, multiple primitives, per-pixel alpha |
| C++ subclass per animation (compile-time) | Bytecode VM (runtime, primitives are built-in) |
| Binary packed struct wire format | Bytecode programs compiled by base station |
| Static channel setup via MQTT | All groups/layers dynamic, created by bytecode |
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
- **Deletion of Channel class** — correct instinct. Replaced by the more flexible Pixel Group + Layer model.

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
- Implement core abstractions: Strip, Pixel Group, Layer, Compositor
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
