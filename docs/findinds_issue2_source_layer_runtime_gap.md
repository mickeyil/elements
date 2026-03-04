# Findings: Issue 2 (Runtime source-layer gap)

## Gap: `source_layer` is decoded but not used by runtime playback code

**Severity:** High

### Evidence
- Compiler emits `source_layer` into each event header:
  - [compiler/elements/blob.py](compiler/elements/blob.py)
  - [compiler/elements/compiler.py](compiler/elements/compiler.py)
- Decoder stores it in the decoded event:
  - [src/decoder.h](src/decoder.h)
  - [src/decoder.cpp](src/decoder.cpp)
- Current runtime code in `src` does not instantiate animations from `Program` events, and does not read `AnimationEvent::source_layer` during playback:
  - [src/decoder.cpp](src/decoder.cpp)
  - [src/main.cpp](src/main.cpp)
  - [src/compositor.cpp](src/compositor.cpp)
  - [src/layer.cpp](src/layer.cpp)

### Impact
Even though compile-time and blob format now correctly model `source_layer`, playback still cannot reproduce source-dependent shifts as intended because there is no runtime wiring to fetch source buffers based on `source_layer`.

### Required fix for Issue 2
1. Implement runtime event-to-animation construction path (e.g. engine/cursor loop) that reads each `AnimationEvent` from decoded `Program`.
2. In creation path, resolve source buffer when `event.source_layer != SOURCE_NONE`:
   - `source_layer` -> source layer buffer pointer
   - `source_length` -> source layer index map length
3. For `ANIM_SHIFT`, pass source buffer reference into constructor logic so it snapshots source data into work buffer.
4. Ensure render loop preserves rendering order and layer buffers so that `source_layer <= dependent_layer` guarantee is respected.
5. Add runtime test(s) that validate a dependent shift reads source pixels from the modeled source layer.

### Notes
- This is separate from compiler/decoder format correctness, which is now aligned.
- Issue #1 is done through encoding/decoding; Issue #2 is runtime consumption and behavior wiring.
