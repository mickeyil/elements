# Elements

Beat-synced LED animation engine targeting ESP32. Work in progress.

**Docs:** [firmware & engine](drafts/firmware.md) · [controller](drafts/controller.md) · [protocol](drafts/protocol.md) · [web app](docs/web_app.md) · [future plans](docs/draft_future_plans.md)

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

The old `network_sim` smoke path is deprecated and staged under
`src/deprecated/`. The v3 sim launcher is in implementation, so the
local multi-process smoke path is temporarily unavailable.

## Runtime Notes

- `web` starts the web UI server and connects to the controller as an observer client, issuing playback commands over transient writer connections
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
src/sim/          active host/sim platform implementations
src/deprecated/   old simulator stack staged for deletion after v3
test/             Catch2 tests
test/deprecated/  tests for old simulator stack, staged with deprecated code
compiler/         Python compiler (DSL → binary blob)
controller/       Python controller service, web UI server
controller/web_ui/ Vue 3 browser interface source + built assets
docs/             Documentation
build/            Disposable build output (cmake, PlatformIO)
local/            Repo-local cache and tool state (venv, PlatformIO workspace)
instance/         Local deployment config and simulator layouts (gitignored)
```
