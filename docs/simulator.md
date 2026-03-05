# Elements — Simulator Design

The simulator is a Python/pygame tool that visualizes a multi-strip program without hardware. It decodes compiled blobs, runs animation rendering in Python (mirroring the C++ engine), and displays the strips as a side-by-side LED grid updated at 50Hz.

---

## Goal

Feed it the output of `build()` (one blob per strip), see all strips animating in a GUI. Useful for:
- Validating DSL programs before deploying to hardware
- Iterating on animation params and timing
- Demonstrating multi-strip sync visually

---

## Architecture

```
blobs: dict[str, bytes]
    ↓ decode_blob() [existing: compiler/elements/blob.py]
decoded: dict[str, Program]
    ↓ StripEngine.tick(t) [per strip]
rgb_buffers: dict[str, list[RGB]]
    ↓ pygame display
Window: N horizontal rows of colored circles (one row per strip)
```

---

## Components

### `simulator/animations.py` — Python animation renderers

Mirrors the C++ animation logic. Each function writes into a buffer of `(h, s, v, a)` tuples.

```python
def render_wave(buf, length, t_rel, params): ...
    # val = min_val + (max_val - min_val) * 0.5 * (1 + sin(2π*(t_rel/period + phase0 + pixel_step*i)))
    # writes to params["channel"] (H, S, or V); other channels from fixed params

def render_spark(buf, length, t_rel, params): ...
    # color from params, alpha = max(0, 1 - t_rel / fade)

def render_fill(buf, length, t_rel, params): ...
    # solid color, alpha=1

def render_shift(buf, length, t_rel, params, work_buf): ...
    # pixel_offset = velocity * t_rel
    # for each i: src = (i + pixel_offset) % length (circular) or fill color
    # output[i] = work_buf[floor(src)] (linear interp optional)
```

### `simulator/engine.py` — Python engine

Mirrors the C++ engine tick loop. One `StripEngine` per strip.

```python
class StripEngine:
    def __init__(self, program: dict):
        # program = decoded blob (from decode_blob())
        # allocates layer buffers, buffer pool, sets cursors to 0

    def tick(self, t: float) -> list[tuple[int,int,int]]:
        # For each layer:
        #   - advance cursor past ended events
        #   - if event active: create/continue animation, render into buffer, remap
        # Composite layers (bottom-to-top, alpha blend, HSV→RGB)
        # Returns list of (R, G, B) for each physical LED in the strip's index_map
```

Key implementation notes:
- Animation instances are plain dicts or dataclasses (no class hierarchy needed in Python)
- Shift: read the source layer's buffer at activation (same logic as C++ `AnimShift` constructor)
- HSV→RGB: use `colorsys.hsv_to_rgb(h/360, s, v)` (h is 0-360 in params, colorsys expects 0-1)
- Alpha blend: `out_rgb = lerp(below_rgb, pixel_rgb, alpha)` per pixel

### `simulator/display.py` — Pygame display

```python
class SimDisplay:
    def __init__(self, strip_names: list[str], strip_lengths: list[int],
                 led_radius: int = 8, fps: int = 50): ...

    def update(self, rgb_buffers: dict[str, list[tuple[int,int,int]]]): ...
        # Draw each strip as a horizontal row of colored circles
        # Strip label on the left
        # Background: dark gray

    def poll_events(self) -> bool:
        # Returns False if window closed or Esc pressed
```

Layout: strips stacked vertically, with a small gap between rows. Optional time/BPM readout at the top.

### `simulator/__main__.py` — Entry point

```
Usage:
  python -m simulator <program.py>          compile DSL, run simulator
  python -m simulator <a.blob> <b.blob>...  load pre-compiled blobs
```

Main loop:
```python
clock = pygame.time.Clock()
t0 = time.monotonic()
while display.poll_events():
    t = time.monotonic() - t0
    if t > program_duration:
        t = 0.0  # loop
        t0 = time.monotonic()
    rgb_buffers = {name: engine.tick(t) for name, engine in engines.items()}
    display.update(rgb_buffers)
    clock.tick(50)
```

---

## Implementation notes

- Keep `simulator/` as a top-level directory (sibling to `compiler/`, `src/`, `docs/`)
- Depends only on stdlib + pygame; import `decode_blob` from `compiler.elements.blob`
- No C++ code involved — all rendering is Python
- Target: 50Hz on an i3-10110U with 2-3 strips of 150 LEDs each (well within pygame's capacity for this workload)

---

## Example DSL for testing the simulator

```python
from elements.dsl import *

strip_a = strip("tower_left", length=50)
strip_b = strip("tower_right", length=50)

# Slow blue wave on the left, slow green wave on the right
left_wave = wave(channel="V", h=220, s=1.0, v=0.8,
                 min_val=0.2, max_val=1.0, period=4, phase0=0.0, pixel_step=0.05)
right_wave = wave(channel="V", h=120, s=1.0, v=0.8,
                  min_val=0.2, max_val=1.0, period=4, phase0=0.0, pixel_step=0.05)

# White sparks on left, yellow sparks on right, beat-aligned
left_spark  = spark(color="white",  fade=0.2)
right_spark = spark(color="yellow", fade=0.2)

left_wave.schedule( strip_a.pixels("0-49"), at=0, duration=8)
right_wave.schedule(strip_b.pixels("0-49"), at=0, duration=8)

for beat in range(8):
    left_spark.schedule( strip_a.pixels("10,25,40"), at=beat,       duration=sec(0.1))
    right_spark.schedule(strip_b.pixels("10,25,40"), at=beat + 0.5, duration=sec(0.1))

blobs = build(beat=0.5, duration=4.0)
# blobs = {"tower_left": bytes, "tower_right": bytes}
```
