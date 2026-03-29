# Draft — Future Plans

Unimplemented or partially-implemented features, extracted from earlier design docs. This file preserves design intent for future planning. The git history has the full original docs.

---

## Ambient Mode

Standalone playback of pre-programmed background animations. A bytecode program that loops indefinitely, stored on device flash (LittleFS) for power-on default. Can be replaced anytime via LOAD. Think: slow color waves, gentle breathing, gradient sweeps.

Not yet implemented — devices currently require a controller connection to receive programs.

---

## Audio-Player Integration

The controller manages audio playback alongside LED animations:

1. Base station pre-analyzes audio offline (beat detection, energy analysis, frequency decomposition)
2. Results compiled into animation timeline
3. Controller coordinates START(t0) across audio and all devices
4. Seek: `seek_and_start_at(t_rel, t0)` for both audio and devices simultaneously

Audio latency compensation: the base station accounts for its own audio pipeline latency using ALSA `snd_pcm_delay()` or PipeWire latency queries. `T_speaker = T_write_to_buffer + (buffered_frames / sample_rate)`.

Sync tolerance target: ±10ms (human perception threshold for beat-sync is ~20ms).

ESP32 crystal drift: ~20-40 ppm → 6-12ms over 5 minutes, within tolerance with periodic sync corrections.

Previous experimentation: `wavplayer` project demonstrated precise control of Linux audio APIs with verified sync via slow-motion video capture.

---

## Web Playback Control Parity

Extend `elemctl web` beyond observer mode to support:

- Browser-side playback controls (load, play, pause, stop, seek)
- Browser-side program/scene submission
- Browser-side device provisioning UX

Currently the browser can watch animations, manage devices, and edit layouts — but all playback control goes through the TUI.

---

## Device Provisioning UX

- Keep the full hardware UID (MAC-based) as the canonical internal identity
- Allow operators to identify devices by a short human-facing suffix label (e.g., last 3 MAC bytes like `0A11E3`)
- Resolve short IDs to the full UID only when the suffix is unique
- On suffix collision, fall back to longer/full UID instead of renaming

---

## Browser Layout Editor Backlog

### Polish
- Selection clarity and drag/reshape affordances
- Context-menu improvements
- Warning copy and operator feedback

### Features
- Polyline / multi-segment strip paths
- Richer primitive editing
- Cross-device installation view
- Pre-load inactive-LED validation

---

## Additional Animation Types

Mentioned in earlier design but not yet implemented:

- **gradient** — linear gradient between two colors (stateless)
- **sweep** — band of color moving along pixels (stateless)

---

## Cross-Strip Splitting

Currently, stateless animations (wave, spark, paint) are scheduled independently per strip. A single animation spanning N strips as one continuous visual effect (e.g., a wave that flows across two physical strips) would require the compiler to split pixel groups across strip boundaries. Shift cross-strip is a harder problem (needs a complete pixel snapshot at activation, can't be split across independent blobs).

---

## Animation Instance Pool

The compiler knows the max concurrent animations. The engine could pre-allocate a fixed pool and use placement-new instead of `new`/`delete` per event activation. Not implemented — current per-event allocation works fine.

---

## Milestones from Roadmap (as of 2026-03-29)

### Milestone A: First real ESP32 end-to-end playback
Goal: one real ESP32 device fully working through the controller path. Firmware, controller connection, LOAD/PLAY on real LEDs, disconnect recovery.

Status: firmware and controller code is implemented; hardware validation in progress.

### Milestone B: Real-hardware diagnostics and operator UX
Better per-device diagnostics, clearer status surfaces, targeted hardware smoke tests, device provisioning UX (short suffix labels).

### Milestone C: Web control parity
Browser-side playback/session controls, program/config mutations from the browser.

### Milestone D: Clock sync validation
Validate the implemented sync protocol on real hardware. Tune filter parameters under real WiFi conditions.

### Milestone E: Audio-player integration
Implement the controller ↔ audio contract, align audio with playback/session control.
