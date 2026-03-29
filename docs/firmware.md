# Firmware & Engine

The ESP32 firmware receives compiled animation blobs over TCP, decodes them into memory, and plays them back on a WS2812B LED strip. A desktop simulator (`network_sim`) uses the same engine with the same wire protocol, so you can develop without hardware.

## Pipeline

```
Binary blob (from controller)
  → Decoder (parse into Program struct)
    → Engine (advance timeline, manage animation lifecycle)
      → Animations (render HSVA pixels into layer buffers)
        → Compositor (blend layers, HSV→RGB, gamma)
          → Strip (LED output)
```

## Core Abstractions

**Program** — the top-level struct decoded from a blob. Contains layers, a buffer pool for stateful animations, duration, and a scratch buffer for remap operations. One program loaded at a time; loading a new one replaces the old.

**Layer** — a pixel buffer with an index map to physical LED positions. Layers are static (created at load, live until teardown). Events within a layer never overlap in time. Layer index = compositing priority (0 = bottom).

**Animation** — an instance that writes HSVA pixels into a layer buffer. Created when an event activates, destroyed when it ends. Base class with virtual `render(buffer, length, t_rel)`. Four types:

| Type | What it does | Stateful? |
|------|-------------|-----------|
| **wave** | Sine modulation on one HSV channel | No |
| **spark** | Flash + alpha fade-out | No |
| **paint** | Solid color or per-pixel palette | No |
| **shift** | Slide pixels left/right from a snapshot | Yes (work buffer) |

**Compositor** — blends active layers bottom-up into the Strip using per-pixel alpha in RGB space, then applies gamma correction.

**Engine** — owns the Program and Compositor. Each frame, for each layer: advance a cursor past finished events, create animation instances on activation, render, scatter-copy if remapped. O(1) per layer per frame (cursor only moves forward).

## Source Layer Dependencies

Any event can declare `source_layer` to read another layer's pixel buffer. The engine passes the source buffer to the animation constructor. Shift uses this to snapshot pixels from a prior wave. Ordering invariant: `source_layer < dependent_layer` (compiler-enforced). `SOURCE_NONE = 0xFF` when unused.

## Device State Machine

```
       LOAD              START              end of program
  IDLE ────→ LOADED ──────→ PLAYING ──────────→ ENDED
               ↑  ↑          ↓   ↑                │
               │  │  PAUSE   │   │ RESUME          │
               │  │          ↓   │                 │
               │  │        PAUSED                  │
               │  └── STOP ──┘                     │
               └────────── LOAD ───────────────────┘
```

LOAD tears down any current program and replaces it. PAUSE/RESUME preserve engine state. STOP clears to black, resets to t=0, stays LOADED. JUMP resets the engine and seeks to a safe time.

## Firmware Architecture (ESP32)

Entry point: `src/firmware/main.cpp` → `FirmwareApp.begin()` / `run_once()`.

| File | Role |
|------|------|
| `firmware_app.h/cpp` | Orchestrator: WiFi, discovery, TCP, device lifecycle |
| `esp_device.h/cpp` | `PlaybackDevice` subclass: FastLED output, `esp_timer_get_time()` clock |
| `controller_connection.h/cpp` | TCP command parser, ACK responses, state machine |
| `discovery_service.h/cpp` | UDP HELLO broadcasts, sync probe responses |
| `wifi_manager.h/cpp` | WiFi connection with credential caching (NVS) |
| `device_identity.h/cpp` | MAC-based UID, random boot token |
| `wire_constants.h` | Shared protocol constants (ports, command codes, timeouts) |
| `diagnostics.h/cpp` | Serial logging, periodic status dumps |

## Shared C++ Core (`src/`)

Used by both firmware and simulator:

| File | Role |
|------|------|
| `playback_device.h/cpp` | Abstract base: state machine, blob loading, tick loop, sync offset |
| `decoder.h/cpp` | Binary blob parser → `Program` struct tree |
| `engine.h/cpp` | Timeline cursor, animation lifecycle, remap scatter-copy |
| `compositor.h/cpp` | Layer blending, HSV→RGB, gamma LUT |
| `animation.h` | Abstract base class |
| `anim_wave.h` | Wave animation |
| `anim_spark.h` | Spark animation |
| `anim_paint.h` | Paint animation |
| `anim_shift.h` | Shift animation (stateful) |
| `colors.h/cpp` | `hsva_t`, `rgb_t`, conversion, gamma table |
| `strip.h` | RGB buffer wrapper |
| `hardware_profile.h` | Strip length constraint (max 250 pixels) |

## Desktop Simulator

`network_sim` (`src/network_sim.cpp`) is a standalone binary that wraps `ESPSimulated` with the same TCP/UDP protocol as real firmware. It sends HELLO packets, accepts commands, and streams RGB frames back over UDP. Use it for development without hardware:

```bash
./build/network_sim --device-uid sim-1 --tcp-port 6053 \
  --discovery-port 6040 --discovery-host 127.0.0.1
```

`ESPSimulated` (`src/esp_simulated.h/cpp`) extends `PlaybackDevice` with queue-based frame capture and debug seek (replay from t=0).

`strip_render` (`src/strip_render.cpp`) is an offline CLI tool that reads a blob from stdin and writes frames to stdout.

## Binary Blob Format

Header (12 bytes, little-endian): magic `"ELEM"`, version (2), layer_count, buffer_count, max_remap_length, duration (f32).

Then: buffer pool sizes, followed by per-layer data (index map + events). Each event contains: anim type, timing, source_layer, remap, and type-specific params. All little-endian, no byte swapping needed on ESP32 or x86.

## Tests

Catch2 tests in `test/`. Fixtures auto-generated by `test/fixtures/generate.py` (requires Python compiler).

```bash
cmake -B build && cmake --build build && cd build && ctest
```
