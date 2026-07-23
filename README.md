# Elements

LED animation engine for ESP32 devices. Animations are written in a small
Python DSL, compiled to compact binary blobs, and played by a C++ engine that
runs identically on real hardware and on a host simulator, so animations can
be developed and previewed in the browser with no hardware at all. A central
controller distributes programs and keeps every device on a shared synced
clock, so multiple devices play in lockstep.

## AI Assistance
This project was made with the assistance of Codex & Claude models. Some parts
got more scrutiny than others:

Current state of quality control over the AI output:
- Most of the C++ parts were fully reviewed with multiple iterations done to design/implementation
  and comments until a satisfied result was achieved. There's still room for improvement.
- Python: controller - partially reviewed, more work is needed.
- Web parts: deemed not important enough for this stage. feel the vibe..

## Architecture

```
DSL (.py) -> compiler -> blob -> controller -> devices (ESP32 / sim) -> LEDs
```

- **`compiler/`** — Python animation DSL and blob compiler.
- **`src/`** — shared C++17 device runtime: discovery, controller link, clock
  sync, blob decoder, playback engine, compositor. Two thin platform shells:
  **`src/firmware/`** (ESP32, PlatformIO + FastLED) and **`src/sim/`** (the
  `sim_device` host binary).
- **`controller/`** — Python controller service: owns session time, discovers
  devices, compiles and distributes programs, answers clock sync.
- **`controller/web_ui/`** — Vue 3 web UI: live device preview and playback
  control.

## Prerequisites

Python >= 3.10, Node >= 20.19 (with npm), CMake >= 3.14, a C++17 compiler,
git.

```bash
sudo apt-get install build-essential cmake git python3 python3-venv nodejs npm
```

Check `node --version` afterwards; distro-packaged Node may be older than
20.19. The ESP32 firmware additionally needs
[PlatformIO](https://platformio.org/); it is not required for the simulator.

## Setup

```bash
./elemctl setup
```

Run it online: it creates a Python venv at `local/venv`, builds the web UI
into `local/web_dist`, and builds the native `sim_device` binary into
`build/`. It is idempotent; re-run it after a pull. Everything generated
lives in the gitignored `build/`, `local/`, and `instance/` directories.

## Run

In three terminals:

```bash
./elemctl server      # controller
./elemctl sim sim-1   # a simulated device
./elemctl web         # web UI at http://localhost:8080
```

Open http://localhost:8080, add device `sim-1` (strip id `ring8`, length 8),
then load `ring8_blue_wave` and play. Animation sources live in
`animations/`; add or edit `.py` files there and rescan from the UI.

ESP32 hardware: `make firmware` builds, `make flash` uploads.
