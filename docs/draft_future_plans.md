# Draft — Future Plans

Unimplemented or partially-implemented features, extracted from earlier design docs. This file preserves design intent for future planning. The git history has the full original docs.

---

## Ambient Mode — Implemented

Standalone playback of pre-programmed background animations. A compiled strip blob stored on device flash (LittleFS) that loops indefinitely at boot and after controller detach. Provisioned via `STORE_BACKGROUND` command from the controller. Cleared via `CLEAR_BACKGROUND`.

Current behavior: at boot, the device attempts background startup before WiFi/discovery bring-up, self-applying the stored strip_length as its hardware profile if none is set. Controller attachment preempts background. After detach: grace hold (5s) → blank → background resumes. Background loops locally without controller involvement.

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

## Completed Infrastructure Milestones

The following milestones from the detach/fallback/rejoin roadmap have been implemented:

- **Controller-owned session timebase** — controller owns the canonical show clock, no longer derives time from devices
- **Retained sessions + per-device detach** — session survives device disconnects, three-state participant model (member/attached/serving)
- **Firmware detached-mode behavior** — grace hold → blank on controller loss
- **Background provisioning + runtime** — persistent background blob storage (LittleFS), auto-play at boot and after detach
- **Snap-to-safe rejoin** — detached devices automatically rejoin at a safe point when they reconnect, sync-gated for ESP32

## Remaining Feature Milestones

### Real-hardware diagnostics and operator UX
Better per-device diagnostics, clearer status surfaces, targeted hardware smoke tests, device provisioning UX (short suffix labels).

### Web control parity
Browser-side playback/session controls, program/config mutations from the browser.

### Clock sync validation
Validate the implemented sync protocol on real hardware. Tune filter parameters under real WiFi conditions.

### Audio-player integration
Implement the controller ↔ audio contract, align audio with playback/session control.
