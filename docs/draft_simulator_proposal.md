# Simulator Design — pybind11 + Flask + Browser

## Goal

Run the **actual C++ engine pipeline** (decoder → engine → compositor → strip) without LED hardware, and visualize output as colored squares in a browser. A local dev tool for previewing and debugging animations.

Key requirements:
- Exact runtime parity with the ESP hardware path (no Python reimplementation of animations)
- Interactive playback: play/pause, scrub bar, frame stepping
- Multi-strip support (shared clock)
- Gamma correction disabled (reserved for physical LED behavior, not monitors)

---

## Why pybind11 + Flask

The C++ engine is mature and should be reused directly to avoid logic drift. The question is how to get its output to a display.

Three architectures were evaluated:

| Approach | Pros | Cons |
|---|---|---|
| **C++ serves everything** (mongoose/crow) | Single process, no IPC | C++ is painful for web serving, config, routing. Every change = recompile. |
| **Flask + separate C++ process** | Python for web concerns | Two processes, IPC glue for controls, startup coordination, two ports. |
| **Flask + pybind11** (chosen) | Single process, no IPC, no C++ networking libs. Flask handles web natively. Binding is ~50 lines. | pybind11 build step. GIL is a non-issue at this scale. |

The chosen path: pybind11 wraps the C++ engine as a Python extension module. Python orchestrates everything — tick loop, playback state, WebSocket streaming — and calls into C++ only for the heavy work (`engine.tick()` and reading the strip buffer).

---

## Abstractions and ownership

```
┌──────────────────────────────────────────────────────┐
│  Python Simulator (owns everything, single process)  │
│                                                      │
│  Per strip:                                          │
│  ┌──────────┐  ┌───────┐  ┌────────────────────┐    │
│  │ rgb_buf   │  │ Strip │  │ Engine             │    │
│  │ bytearray │←─│ (view)│←─│ (tick, render,     │    │
│  │ Python    │  │  C++  │  │  composite)    C++ │    │
│  │ owns this │  │       │  │                    │    │
│  └──────────┘  └───────┘  └────────────────────┘    │
│                                                      │
│  Shared:                                             │
│  ┌──────────────┐  ┌────────────────────┐            │
│  │ Flask         │  │ WebSocket          │            │
│  │ (static files)│  │ (frames + controls)│            │
│  └──────────────┘  └────────────────────┘            │
└──────────────────────────────────────────────────────┘
```

**`rgb_buf`** — `bytearray(strip_length * 3)`. Python allocates this. After `engine.tick()`, these bytes contain the final rendered RGB frame. Same role as the `CRGB crgb[]` array on ESP.

**`Strip`** — C++ thin wrapper (`src/strip.h`). Doesn't allocate, doesn't own. Provides `set_rgb(i, color)` / `get_rgb(i)` over the buffer pointer it was given. On ESP, FastLED owns the buffer. In the simulator, Python owns it.

**`Engine`** — C++ class (`src/engine.h`). Owns `Program*` (freed on destroy), owns layer state (cursors, animation instances). On `tick(t)`, runs the cursor loop, creates/destroys animation instances, calls render, composites layers, writes final RGB into Strip.

**`Program*`** — C++ struct from `decode_program()`. Engine takes ownership. Once passed to Engine, Python must not free it (pybind11 handles this via ownership transfer).

**Flask** — serves `index.html`, layout JSON, static assets.

**WebSocket** — bidirectional: sends binary frames to browser, receives JSON control commands from browser.

---

## End-to-end walkthrough: wave on 10 pixels

### Setup

A `.bin` blob containing a single wave animation: 10 pixels, 4 seconds, single layer.

### Initialization

```python
# 1. Read the blob
blob_bytes = open("wave_test.bin", "rb").read()

# 2. Decode — C++ parses blob into Program struct
program = elements_cpp.decode_program(blob_bytes)
# program.duration = 4.0, program.layer_count = 1

# 3. Allocate strip buffer (Python owns this memory)
strip_length = 10
rgb_buf = bytearray(strip_length * 3)   # 30 zero bytes

# 4. Create Strip — C++ view over Python's buffer
strip = elements_cpp.Strip(rgb_buf, strip_length)

# 5. Create Engine — takes Program + Strip reference
engine = elements_cpp.Engine(program, strip)
# internally: allocates layer buffers (hsva_t[10]),
# sets cursor=0, instance=None

# 6. Playback state (Python-side)
playing = False
current_t = 0.0
duration = program.duration   # 4.0
dt = 1.0 / 50                # 0.02s

# 7. Start Flask + WebSocket, serve index.html
app.run()
```

Browser connects, loads layout JSON, draws 10 black squares with thin gray borders. Nothing moves yet.

### User presses Play

```
Browser                    Python                      C++
   │                         │                          │
   │  {"cmd":"play"}         │                          │
   │────────────────────────>│                          │
   │         (WebSocket)     │                          │
   │                         │  playing = True          │
   │                         │  t0 = time.monotonic()   │
   │                         │                          │
```

### Tick loop — one frame at t=0.3s

```python
# Main loop (background thread, 50Hz)
while True:
    if playing:
        current_t = time.monotonic() - t0
        engine.tick(current_t)                          # → into C++
        ws.send(pack_frame(current_t, rgb_buf))         # read buffer, send
    clock.tick(50)
```

Inside `engine.tick(0.3)`:

```
engine.tick(0.3)
│
├─ Layer 0, cursor=0
│  │
│  ├─ Event 0: wave, t_start=0.0, duration=4.0
│  │  t=0.3 < end=4.0 → ACTIVE
│  │
│  ├─ instance is None → create AnimWave
│  │  AnimWave stores: channel=V, h=220, s=1.0, v=0.0,
│  │                   min_val=0.0, max_val=0.4, period=4.0, ...
│  │
│  ├─ t_rel = 0.3 - 0.0 = 0.3
│  │  remap_is_identity = true → render directly into layer buffer
│  │
│  ├─ AnimWave::render(layer_buffer, 10, 0.3)
│  │  │
│  │  │  base_phase = (2π × 0.3 / 4.0) + phase0
│  │  │
│  │  │  for each pixel i=0..9:
│  │  │    phase = base_phase + i × pixel_step
│  │  │    val = min_val + range × (sin(phase) × 0.5 + 0.5)
│  │  │    layer_buffer[i] = {h=220, s=1.0, v=val, a=1.0}
│  │  │
│  │  │  Result (HSVA, in layer buffer):
│  │  │    pixel 0: {220, 1.0, 0.02, 1.0}  ← dim blue
│  │  │    pixel 1: {220, 1.0, 0.38, 1.0}  ← brighter blue
│  │  │    pixel 2: {220, 1.0, 0.02, 1.0}  ← dim
│  │  │    ...alternating pattern from pixel_step=PI
│  │
│  └─ active_mask |= (1 << 0) = 0x01
│
├─ Compositor::composite(layers, 1, active_mask=0x01)
│  │
│  │  strip.clear()       → rgb_buf = all zeros
│  │
│  │  Layer 0 is active. For each pixel i=0..9:
│  │    hsva = layer_buffer[i]
│  │    rgb = hsv_to_rgb(220, 1.0, 0.02) → (0, 1, 5)
│  │    alpha = 1.0 → full overwrite
│  │    strip.set_rgb(i, rgb)
│  │                  │
│  │                  └─ writes directly into rgb_buf:
│  │                     rgb_buf[0..2]  = [0, 1, 5]    pixel 0, dim
│  │                     rgb_buf[3..5]  = [0, 4, 97]   pixel 1, bright
│  │                     rgb_buf[6..8]  = [0, 1, 5]    pixel 2, dim
│  │                     ...
│  │
│  │  (gamma skipped — simulator mode)
│  │
│  └─ Done. rgb_buf now contains final RGB.
│
└─ returns true (program not ended)
```

Back in Python — `rgb_buf` was modified in-place by C++ through the Strip pointer:

```python
# Pack and send — no copy needed, rgb_buf already has the data
frame = struct.pack('<f', current_t) + bytes(rgb_buf)
#        4 bytes: 0.3 (float32)       30 bytes: RGB
# total: 34 bytes over WebSocket binary message

ws.send(frame, binary=True)
```

### Browser receives and renders

```
Python                         Browser
  │                              │
  │  [0.3f][0,1,5,0,4,97,...]   │
  │─────────────────────────────>│
  │       (34 bytes, binary)     │
  │                              │  Parse:
  │                              │    t = view.getFloat32(0)  → 0.3
  │                              │    rgb = new Uint8Array(buf, 4)
  │                              │
  │                              │  Update clock: "0:00.30"
  │                              │  Update scrub bar: 0.3 / 4.0 = 7.5%
  │                              │
  │                              │  For each pixel i:
  │                              │    pos = layout[i]
  │                              │    r = rgb[i*3], g = rgb[i*3+1], b = rgb[i*3+2]
  │                              │    ctx.fillStyle = `rgb(${r},${g},${b})`
  │                              │    ctx.fillRect(pos.x, pos.y, size, size)
```

Canvas shows 10 squares — alternating dim/bright blue. 20ms later the next frame arrives, the pattern has shifted, animation is alive.

### Seek (user drags scrub bar to t=2.5)

```
Browser                    Python                         C++
   │                         │                              │
   │ {"cmd":"seek","t":2.5}  │                              │
   │────────────────────────>│                              │
   │                         │                              │
   │                         │  engine.reset()   ──────────>│ zero cursors,
   │                         │                              │ delete instances,
   │                         │                              │ zero layer buffers
   │                         │                              │
   │                         │  # replay frame-by-frame     │
   │                         │  for t in [0.02, 0.04, ...   │
   │                         │            2.48, 2.50]:      │
   │                         │      engine.tick(t) ────────>│ (125 ticks,
   │                         │                              │  ~microseconds)
   │                         │                              │
   │                         │  current_t = 2.5             │
   │                         │  # send final frame only     │
   │  [2.5f][rgb...]         │                              │
   │<────────────────────────│                              │
   │                         │                              │
   │  clock: "0:02.50"       │                              │
   │  scrub bar: 62.5%       │                              │
```

Reset + replay is always correct because it reproduces the exact sequence of animation creation, source layer snapshots, and cursor advancement. Cost: 125 C++ ticks ≈ microseconds.

### Program loop (auto-restart at duration boundary)

When `engine.tick(t)` returns `false` (t >= duration):

```python
if not engine.tick(current_t):
    engine.reset()
    t0 = time.monotonic()
    current_t = 0.0
    engine.tick(0.0)
```

---

## Frame protocol

### On connection (JSON, sent once)

```json
{
  "strips": [
    {"name": "main", "length": 10},
    {"name": "tower", "length": 50}
  ],
  "duration": 4.0,
  "fps": 50
}
```

Browser uses this to scale the scrub bar and validate layout.

### Per-frame (binary WebSocket, sent at up to 50Hz per strip)

```
[uint8:  strip_index]     1 byte
[float32: t]              4 bytes
[uint8[3*n]: rgb_data]    3*n bytes
```

For a 10-pixel strip: 1 + 4 + 30 = 35 bytes per frame. For two strips of 50 pixels: 2 × (1 + 4 + 150) = 310 bytes per frame. At 50Hz: ~15 KB/s. Trivial.

### Control commands (JSON, browser → server)

```json
{"cmd": "play"}
{"cmd": "pause"}
{"cmd": "seek", "t": 2.5}
{"cmd": "step", "dir": 1}      // +1 forward, -1 backward
```

---

## Frontend

Single `index.html` with inline or adjacent JS. No framework.

### Canvas

- Black background
- LED positions from layout JSON (loaded by browser, server doesn't need it)
- Inactive LEDs: black fill, thin gray border
- Active LEDs: colored fill from RGB frame data

### Controls bar (bottom)

- Play / Pause toggle button
- Clock display: `M:SS.cc` (driven by `t` from frame packets, never computed locally)
- Scrub bar: position = `t / duration`, draggable
- Step backward / Step forward buttons (enabled only when paused)

### Layout config (JSON, loaded by browser)

```json
{
  "strips": {
    "main": {
      "pixels": [[10, 20], [30, 20], [50, 20]]
    }
  }
}
```

Server sends indexed RGB data. Browser maps pixel indices to (x, y) positions. This keeps layout purely a frontend concern.

---

## Playback state machine (Python-side)

```
         ┌──────────────────────────────────┐
         │                                  │
    ┌────▼─────┐  play   ┌──────────┐      │
    │  PAUSED  │────────>│ PLAYING  │      │
    │          │<────────│          │      │
    └──┬───┬──┘  pause   └────┬─────┘      │
       │   │                   │            │
  step±1  seek(t)         t >= duration     │
       │   │                   │            │
       │   └──>reset+replay    └──>reset    │
       │       send frame          loop─────┘
       │
       └──>reset+replay
           send frame
```

- **Playing:** tick at 50Hz, send every frame. On reaching duration, reset + loop.
- **Paused:** no ticking. Respond to seek and step commands only.
- **Seek:** always reset + replay from 0 to target time. Send final frame.
- **Step:** reset + replay to `current_t ± dt`. Send final frame.

---

## Required C++ changes

### 1. `Engine::reset()` — new method

Needed for seek/scrub/step/loop. The current `tick(t)` is forward-only; cursor and instance state cannot go backward.

Must:
- Zero all layer cursors to 0
- Delete all active animation instances (set to nullptr)
- Zero all layer buffers (hsva_t arrays)
- Zero all buffer pool entries (shift work buffers)

After reset, the engine is in the same state as after construction. Seek is then: `reset()` → replay `tick(dt), tick(2*dt), ..., tick(target)`.

Must verify: source-dependent animations (shift snapshotting a source layer) reproduce correct behavior after reset + replay, since the source layer is re-rendered during replay. This should work by construction but needs an explicit test.

### 2. Compositor gamma flag

Current `Compositor::composite()` applies `gamma_correct()` unconditionally. The simulator must disable it.

Add `bool _gamma_enabled` to Compositor (default `true`, set `false` for simulator). One if-guard:

```cpp
if (_gamma_enabled) {
    for (uint16_t i = 0; i < _strip.length(); i++)
        _strip.set_rgb(i, gamma_correct(_strip.get_rgb(i)));
}
```

### 3. pybind11 binding module

Expose to Python (~50 lines of glue):

- `decode_program(bytes) → Program*` — ownership transfers to Engine on construction
- `Engine(Program*, Strip&)` — takes ownership of Program
- `Engine::tick(float) → bool`
- `Engine::reset()`
- `Strip(buffer, length)` — wraps a Python-owned bytearray
- `Program::duration` (read-only property)

The ownership handoff (Program → Engine) must be explicit in the binding to prevent double-free. pybind11 handles this via `std::unique_ptr` or `py::return_value_policy::take_ownership`.

---

## Multi-strip

Shared clock for all strips. Both strips are part of one "show."

Python creates one session (rgb_buf + Strip + Engine) per strip, ticks all of them with the same `t`:

```python
for name, session in sessions.items():
    session.engine.tick(current_t)
    ws.send(pack_frame(session.strip_index, current_t, session.rgb_buf))
```

On seek/reset, all engines are reset and replayed together.

---

## Dependencies

### Python
- **pybind11** — C++ binding (build-time only)
- **Flask** — web server
- **flask-sock** — WebSocket support for Flask

### C++ (existing, no new deps)
- `src/engine.*`, `src/decoder.*`, `src/compositor.*`, `src/strip.*`, `src/anim_*.h`, `src/colors.*`

### Frontend
- Vanilla HTML/CSS/JS. No framework. Canvas 2D is sufficient for 50Hz at typical strip sizes.

### Build
- CMake builds the pybind11 extension module alongside the existing library and test targets.

---

## Files to add/change

### C++ changes
- `src/engine.h` / `src/engine.cpp` — add `reset()` method
- `src/compositor.h` / `src/compositor.cpp` — add `gamma_enabled` flag

### New files
- `simulator/bindings.cpp` — pybind11 module wrapping Engine, Strip, decode_program
- `simulator/app.py` — Flask app, WebSocket handler, tick loop, playback state
- `simulator/static/index.html` — frontend (canvas, controls, WebSocket client)
- `simulator/static/layout.json` — LED position config (example)
- `CMakeLists.txt` — add pybind11 extension target

---

## Open decisions

- **Layout config format:** exact JSON schema for pixel positions (TBD, details to be discussed)
- **Blob loading:** CLI args pointing to `.bin` files for first pass; DSL script execution can come later
- **Flask-sock vs alternatives:** flask-sock is simplest; evaluate if needed
- **Build integration:** CMake pybind11 target setup (FetchContent or find_package)

---

## Acceptance criteria

- Runs 1+ blob files with no hardware
- 50Hz target maintained on typical dev machine
- Browser displays all strips at configured pixel positions
- Play/pause works, clock shows correct frame time
- Scrub bar seeks to arbitrary time correctly
- Step ±1 frame works when paused
- Program loops at duration boundary
- Gamma correction disabled for simulator display
- Source layer dependencies (shift snapshots) reproduce correctly after seek
