# Elements

Beat-synced LED animation engine targeting ESP32. Work in progress.

**Docs:** [firmware & engine](docs/firmware.md) · [controller](docs/controller.md) · [protocol](docs/protocol.md) · [web app](docs/web_app.md) · [future plans](docs/draft_future_plans.md)

## Build

```bash
make build
```

Direct commands still work:

```bash
cmake -B build
cmake --build build
```

Requires C++11 and Python 3 (for test fixture generation).

## Test

```bash
make test
```

Direct commands still work:

```bash
cd build && ctest
```

Python test suites can be run from the repo root:

```bash
pytest
```

Or run individually:

```bash
./build/test_decoder
./build/test_engine
./build/test_colors
./build/test_compositor
./build/test_animations
```

## Local Smoke Test

Build the simulator:

```bash
cmake -B build
cmake --build build --target network_sim
```

Then use four terminals:

```bash
./elemctl server --config examples/single-sim.json
```

```bash
./elemctl sim sim-1 --config examples/single-sim.json
```

```bash
./elemctl tui
```

```bash
./elemctl web
```

In the TUI:

```text
/rescan
/load #1
/play
```

Expected result:
- the `server` terminal logs a discovery line for `sim-1`
- the TUI shows the device come online
- `/rescan` lists `demo_main`
- `/load` and `/play` succeed without manual port/length flags
- `http://127.0.0.1:8080/` shows the running animation in the browser

Notes:
- `tui` connects as the single writer client
- `web` starts the web UI server and connects to the controller as an observer client

- discovery defaults to UDP port `6040` when `controller.discovery_port` is omitted
- set `"discovery_port": null` in the config to disable discovery explicitly
- `elemctl` creates and maintains its managed environment at `local/venv`
- `build/` is disposable build output; `local/` is repo-local cache and tool state
- repo-local deployment config now lives in `instance/config.json`; it is auto-created on first run
- simulator layouts are stored in `instance/layouts/`

## Project structure

```
src/              C++ engine core (decoder, engine, compositor, animations)
src/firmware/     ESP32 firmware (WiFi, TCP, discovery, LED output)
test/             Catch2 tests
compiler/         Python compiler (DSL → binary blob)
controller/       Python controller service, TUI, web UI server
controller/web_ui/ Vue 3 browser interface source + built assets
docs/             Documentation
build/            Disposable build output (cmake, PlatformIO)
local/            Repo-local cache and tool state (venv, PlatformIO workspace)
instance/         Local deployment config and simulator layouts (gitignored)
```
