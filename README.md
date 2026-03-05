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
```

## Project structure

```
src/          C++ engine (decoder, engine, compositor, animations)
test/         Catch2 tests
compiler/     Python compiler (DSL → binary blob)
docs/         Design documents
```
