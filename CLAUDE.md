# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Test Commands

### C++ (desktop build with CMake + Catch2)
```bash
# Configure and build
cmake -B build && cmake --build build

# Run all tests
cd build && ctest          # or: cmake --build build && cd build && ctest

# Run a single test binary
./build/test_decoder
./build/test_engine
./build/test_colors
./build/test_compositor
./build/test_animations
./build/test_playback_device
./build/test_esp_simulated
./build/test_sim_controller
./build/test_background_crc

# Valgrind memcheck (if available; also registered as ctest targets)
valgrind --leak-check=full --error-exitcode=1 ./build/test_engine
```

### Python compiler
```bash
# Bootstrap/update the managed venv
./elemctl setup

# The repo-managed Python environment lives here
source ~/.elements/venv/bin/activate

# Or run directly without activating
/home/mickey/.elements/venv/bin/python -m pytest

# Run compiler tests
cd compiler && python -m pytest

# Regenerate test fixture (blob used by C++ tests)
python test/fixtures/generate.py
```

### Python / elemctl bootstrap
- Prefer `./elemctl ...` as the repo entrypoint on a clean install.
- `elemctl` creates and maintains the managed venv at `~/.elements/venv`.
- If you need to run Python tooling directly, use that venv's interpreter.

### ESP32 firmware (PlatformIO — not used for normal development)
```bash
pio run                    # build
pio run -t upload          # flash
```

## Git Conventions
- Commit messages: 1-line summary, no body, no Co-Authored-By

## Git Workflow
- When asked to commit changes, make a single commit unless explicitly told otherwise. Do not split work into multiple commits.

## Code & Design Discussions
- When discussing design or answering questions, always reference the actual codebase (read the current source files) rather than relying on documentation which may be outdated. The code is the source of truth, not the docs.

## General Rules
- When the user asks to update documentation or discuss design, do NOT implement code changes unless explicitly requested. Distinguish between "doc update" and "code change" tasks.

## Architecture

This is a beat-synced LED animation engine for ESP32. A Python compiler on the base station compiles a DSL program into a binary blob. The ESP32 decodes the blob and plays it back with zero runtime allocation.

### Pipeline
```
DSL (.py) → Python Compiler → Binary Blob → C++ Decoder → Program struct → Engine → Compositor → Strip (LEDs)
```

### C++ runtime (`src/`)
- **`playback_device.h/cpp`** — Abstract base class for devices. State machine (IDLE→LOADED→PLAYING→PAUSED→ENDED), blob loading via decoder, tick loop, sync offset management. `reset_for_detach()` invalidates runtime without presenting. `present_black_frame()` pushes black to output.
- **`decoder.h/cpp`** — Decodes binary blob into a `Program` struct tree (`Program` → `LayerDef[]` → `AnimationEvent[]`). All memory arena-allocated at load time.
- **`engine.h/cpp`** — Drives playback. Each layer has a cursor that monotonically advances through events. Creates `Animation*` instances on-demand via `create_animation()` factory. Takes `float t` (seconds) externally — no internal clock.
- **`compositor.h/cpp`** — Blends active layer buffers into the `Strip` output using per-pixel alpha. Layers composite bottom-up (index 0 = bottom). Optional gamma correction.
- **`animation.h`** — Abstract base class with `virtual render(hsva_t* buffer, uint8_t length, float t)`.
- **`anim_wave.h`, `anim_spark.h`, `anim_shift.h`, `anim_paint.h`** — Concrete animation subclasses. Header-only. `AnimShift` is stateful (uses a work buffer from `BufferPool`).
- **`colors.h/cpp`** — `hsva_t` (float HSVA) and `rgb_t` (uint8 RGB) types, HSV→RGB conversion, gamma correction.
- **`strip.h`** — Thin wrapper over a raw RGB byte buffer (maps to FastLED's CRGB array).
- **`background_crc.h/cpp`** — CRC32 (IEEE/zlib-compatible) for background blob validation.

### ESP32 firmware (`src/firmware/`)
- **`firmware_app.h/cpp`** — Top-level orchestrator. Owns WiFi, discovery, connection, device, and background store. Manages `DeviceMode` state machine: `attached_controlled` → `detached_grace_hold` → `detached_blank` → `detached_background`. On detach: holds last frame, then blanks, then plays background if available. On reattach: controller takes over.
- **`esp_device.h/cpp`** — `PlaybackDevice` subclass: FastLED output, `esp_timer_get_time()` clock.
- **`controller_connection.h/cpp`** — TCP command parser, ACK responses, connection state machine. Handles all wire commands including `STORE_BACKGROUND` and `CLEAR_BACKGROUND`.
- **`background_store.h/cpp`** — Persistent background blob storage. Metadata in NVS/Preferences, blob bytes in LittleFS. CRC32-validated, survives reboots.
- **`discovery_service.h/cpp`** — UDP HELLO broadcasts, sync probe responses.
- **`wifi_manager.h/cpp`** — WiFi connection with credential caching (NVS).
- **`device_identity.h/cpp`** — MAC-based UID, random boot token.
- **`wire_constants.h`** — Shared protocol constants (ports, command codes, timeouts).
- **`diagnostics.h/cpp`** — Serial logging, periodic status dumps with mode.

### Python controller (`controller/elemctl/`)
- **`controller.py`** — Session state machine. Owns canonical session timebase (`_play_t0_ns`), retained session with per-participant state (attached/serving), safe-interval-based rejoin, observer frame suspension when strips are uncovered.
- **`service.py`** — Core service logic: command handlers, compilation, device lifecycle, sync-gated live resume, background provisioning.
- **`network_device.py`** — TCP/UDP device transport. Commands include `store_background()`, `clear_background()`.
- **`clock_sync.py`** — NTP-like sync: probe, filter, correct.
- **`wire.py`** — Wire protocol encode/decode for all commands.

### Python compiler (`compiler/elements/`)
- **`dsl.py`** — User-facing DSL: `strip()`, `wave()`, `shift()`, `spark()`, `paint()`, `build()`. Uses a hidden global `_ProgramBuilder`.
- **`compiler.py`** — Full pipeline: early validation → time resolution (beats→seconds) → layer inference (greedy bin-packing) → buffer packing → late validation → blob emission.
- **`blob.py`** — Binary serialization/deserialization. Blob format documented in docstring. Also contains `decode_blob()` used by Python tests.
- **`types.py`** — Shared types: `AnimDef`, `StripDef`, `PixelGroup`, `SecMarker`, constants.

### Key design invariants
- Controller owns canonical session time — devices follow, not the authority
- Session survives device detach — participants tracked as session-member / transport-attached / actively-serving
- Single cursor per layer, monotonically advancing — O(1) per layer per frame
- `source_layer < dependent_layer` ordering (compiler-enforced)
- `SOURCE_NONE = 0xFF` sentinel when an event has no source dependency
- Per-pixel alpha blending (alpha lives in `hsva_t`, not per-layer)

### Test fixture dependency
C++ decoder and engine tests load `test/fixtures/test_animation.bin`, auto-generated by `test/fixtures/generate.py` (requires the Python compiler). CMake runs this automatically if python3 is found.
