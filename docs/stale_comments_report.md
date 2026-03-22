# Stale Comments Report (2026-03-22)

Generated during docs update sweep. Items below were flagged during source code scan but are uncertain or intentional. Reviewed for discussion.

## Reviewed and left as-is

### src/engine.cpp:52 — Defensive check contradicts guarantee comment

```cpp
// Compiler guarantees pixel subset — si should never be 0xFF
work[i] = (si != 0xFF) ? src_layer.buffer[si] : hsva_t();
```

The comment says the compiler guarantees `si` will never be 0xFF, but the code has a defensive check anyway. This is standard defensive programming — the comment explains the invariant, the code guards against violation. **No change needed** — the comment and code together are clear about intent vs. safety.

### src/strip.h:19 — FastLED/CRGB reference in comment

```cpp
p[0] = c.r;  // CRGB is RGB order; FastLED handles reorder to wire format
```

References FastLED's CRGB even though the simulator path doesn't use FastLED. The comment is still accurate for the intended ESP32 target where FastLED will handle the wire format. **Leave as-is** — relevant for the hardware target.

## Fixed during this sweep

### compiler/elements/compiler.py:148 — Misleading docstring

**Was:** `"""Resolve SecMarkers to beats, then convert all times to seconds."""`
**Now:** `"""Convert SecMarker seconds to beats, then convert all beat values to seconds."""`

The original docstring described the steps in confusing order. The function first converts SecMarker seconds to beats (so everything is in beats), then converts all beat values to seconds.
