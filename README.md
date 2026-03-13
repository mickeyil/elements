# Elements

Beat-synced LED animation engine targeting ESP32. Work in progress.

**[Design document](docs/design.md)** — architecture, abstractions, and links to all detailed docs.

## Build

```bash
cmake -B build
cmake --build build
```

Requires C++17 and Python 3 (for test fixture generation).

## Test

```bash
cd build && ctest
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
- `web` connects as an observer relay and can run alongside the TUI

- discovery defaults to UDP port `6040` when `controller.discovery_port` is omitted
- set `"discovery_port": null` in the config to disable discovery explicitly

## Project structure

```
src/          C++ engine (decoder, engine, compositor, animations)
test/         Catch2 tests
compiler/     Python compiler (DSL → binary blob)
docs/         Design documents
```
