# AGENTS.md

Repo-wide guidance for coding agents working in this repository.

## Working Rules

- When discussing behavior or design, inspect the current source files first. The code is the source of truth; docs may lag behind.
- When the user asks for a documentation update or a design discussion, do not make code changes unless they explicitly ask for implementation.
- When asked to commit, make a single commit unless the user explicitly asks to split the work.
- Commit messages should be a one-line summary only, with no body and no `Co-Authored-By`.
- C++ source files must contain only ASCII characters.
- Don't use `--` as punctuation in comments or docs. Prefer `;`, `:`, parentheses, or separate sentences.
- Repo-local deployment state lives under `instance/`. The default controller config is `instance/config.json`, and simulator layouts live in `instance/layouts/`.

## Build And Test

### C++ Core (desktop CMake + Catch2)
```bash
# Convenience wrapper
make build

# Configure and build
cmake -B build
cmake --build build

# Run all C++ tests
cd build && ctest

# Run individual test binaries
./build/test_decoder
./build/test_engine
./build/test_colors
./build/test_compositor
./build/test_animations
./build/test_playback_device
./build/test_esp_simulated
./build/test_sim_controller
./build/test_background_crc

# Optional Valgrind example
valgrind --leak-check=full --error-exitcode=1 ./build/test_engine
```

### Python / `elemctl`
```bash
# Bootstrap or refresh the managed venv
./elemctl setup

# Run all Python tests from the repo root
pytest

# Run a specific suite
pytest controller/tests/test_service.py -q
pytest compiler/tests/test_compiler.py -q

# Run directly with the managed interpreter if needed
local/venv/bin/python -m pytest
```

- Prefer `./elemctl ...` as the repo entrypoint on a clean checkout.
- `elemctl` creates and maintains the managed venv at `local/venv`.
- `elemctl` auto-creates `instance/config.json` on first run when the default config path is used.
- `pytest.ini` adds `controller/` and `compiler/` to `PYTHONPATH`, so run Python tests from the repo root unless you have a specific reason not to.
- Tests marked `runtime_integration` expect an isolated local runtime: no other `./elemctl server`, `./elemctl sim`, or `network_sim` processes should be running unless you intentionally bypass the guard with `ELEMCTL_TEST_ALLOW_BUSY_RUNTIME=1`.

### Web UI (`controller/web_ui/`)
```bash
cd controller/web_ui && npm test
cd controller/web_ui && npm run build
```

- Source lives in `controller/web_ui/src/`.
- Built assets are written to `controller/web_ui/dist/`.
- `controller/elemctl/web.py` serves `controller/web_ui/dist/` directly, so if you change shipped UI code, keep `dist/` in sync with the source.
- Simulator layout files are stored under `instance/layouts/`.

### ESP32 Firmware (PlatformIO)
```bash
make firmware

pio run
pio run -t upload
```

- Firmware builds are not part of the normal desktop development loop.
- PlatformIO build output is written under `build/pio/`; repo-local PlatformIO workspace state lives under `local/pio/`.

## Project Map

This repository is a beat-synced LED animation engine targeting ESP32. A Python compiler compiles a DSL program into a binary blob. The runtime decodes that blob and plays it back with zero runtime allocation on the device.

### Pipeline
```
DSL (.py) -> Python compiler -> binary blob -> C++ decoder -> Program -> engine -> compositor -> strip
```

### C++ Runtime (`src/`)
- `decoder.h/cpp` decodes the binary blob into a `Program` tree (`Program -> LayerDef[] -> AnimationEvent[]`).
- `engine.h/cpp` advances each layer with a single monotonic cursor and instantiates animations on demand.
- `compositor.h/cpp` blends active layers bottom-up into the strip output with per-pixel alpha.
- `playback_device.h/cpp` owns the runtime state machine and playback lifecycle. `reset_for_detach()` invalidates runtime state without presenting; `present_black_frame()` emits one black frame.
- `animation.h` defines the animation interface; `animations/wave.h`, `animations/spark.h`, `animations/shift.h`, and `animations/paint.h` provide the concrete animation implementations.
- `colors.h/cpp` holds HSVA/RGB types, HSV-to-RGB conversion, and gamma correction.
- `strip.h` is the thin wrapper over the raw RGB byte buffer.
- `background_crc.h/cpp` implements CRC32 for background blob validation.
- `esp_simulated.h/cpp` and `sim_controller.h/cpp` provide host-side simulation support used by tests and the local simulator.

### ESP32 Firmware (`src/firmware/`)
- `firmware_app.h/cpp` is the top-level orchestrator for WiFi, discovery, controller connection, playback device, and background storage.
- `device_mode.h` defines the detached/attached firmware mode state machine.
- `esp_device.h/cpp` is the `PlaybackDevice` implementation backed by FastLED and `esp_timer_get_time()`.
- `controller_connection.h/cpp` handles device wire commands and ACK responses, including background store/clear commands.
- `background_store.h/cpp` persists the detached background blob in NVS/LittleFS with CRC validation.
- `discovery_service.h/cpp`, `wifi_manager.h/cpp`, `device_identity.h/cpp`, `wire_constants.h`, and `diagnostics.h/cpp` provide networking, identity, protocol constants, and diagnostics.

### Python Controller (`controller/elemctl/`)
- `controller.py` owns the canonical session timebase and participant/session state.
- `service.py` handles commands, compilation, device lifecycle, sync-gated resume, and background provisioning.
- `network_device.py` is the TCP/UDP transport to hardware or simulated devices.
- `clock_sync.py` implements NTP-like sync probing and filtering.
- `controller_protocol.py` defines the controller-to-client protocol used by TUI/web clients.
- `device_protocol.py` defines the controller-to-device wire protocol and device status parsing.
- `controller_client.py` is the production Unix-socket client used by the web server and tests.
- `server.py`, `sim.py`, `tui.py`, and `web.py` are the main runtime entrypoints.

### Web UI (`controller/web_ui/`)
- `src/` contains the Vue 3 application source.
- `dist/` contains the built static assets served by `controller/elemctl/web.py`.

### Python Compiler (`compiler/elements/`)
- `dsl.py` exposes the user-facing DSL such as `strip()`, `wave()`, `shift()`, `spark()`, `paint()`, and `build()`.
- `compiler.py` runs validation, beat-to-seconds resolution, layer inference, buffer packing, and blob emission.
- `blob.py` handles binary serialization/deserialization and also provides `decode_blob()` for Python tests.
- `types.py` holds shared compiler types and constants.

## Key Invariants

- The controller owns canonical session time; devices follow it.
- A session survives device detach; transport attachment and active serving are tracked separately.
- Layer playback uses a single monotonic cursor per layer, so runtime work is O(1) per layer per frame.
- Detached firmware modes progress as `attached_controlled -> detached_grace_hold -> detached_blank -> detached_background`.
- Observer clients can receive state and frames but cannot send control commands.
- `source_layer < dependent_layer` ordering is compiler-enforced.
- `SOURCE_NONE = 0xFF` means an event has no source dependency.
- Alpha is per-pixel in `hsva_t`, not a per-layer compositor setting.

## Generated Artifacts

- CMake can auto-generate the C++ test fixtures in `test/fixtures/` when `python3` is available.
- The generated fixtures are:
  - `test_animation.bin`
  - `test_shift.bin`
  - `test_dual_shift_left.bin`
  - `test_dual_shift_right.bin`
- If you change compiler output or fixture generation code, regenerate the fixtures before trusting C++ test results.
- `controller/web_ui/dist/` is generated Vite output, not hand-authored source.
- `build/` is disposable build output; `local/` is repo-local cache and tool state.
- `instance/config.json` and `instance/layouts/` are local deployment state and should remain uncommitted.

## Local Smoke Test

```bash
cmake -B build
cmake --build build --target network_sim
./elemctl server --config examples/single-sim.json
./elemctl sim sim-1 --config examples/single-sim.json
./elemctl tui
./elemctl web
```

In the TUI:

```text
/rescan
/load #1
/play
```
