# PlaybackDevice Redesign — Surviving Ideas

This file used to be the full v2→v3 exploration document. The redesign
has landed: `src/playback.{h,cpp}`, `src/synced_clock.h`,
`Program::requires_sync`, and the v3 controller_link replaced the bulk
of what the original analysis recommended. Deferred work that came out
of the redesign is tracked in `drafts/TODO.md` (`## Playback`).

What remains here is a small set of open ideas that have not turned
into TODOs — keep them for trade-off discussions or as the seed for a
future task.

## ControllerDevice / SimDevice parallel hierarchy

`src/controller_device.h`, `src/sim_device.h`, and
`src/sim_controller.{h,cpp}` exist so the host-side `SimController`
doesn't depend on `PlaybackDevice` internals. The cost is a parallel
hierarchy plus a pure-delegation adapter that mirrors part of the
command surface but omits profile / sync / detach.

After the post-step-20 owner rewire collapses `PlaybackDevice` /
`ESPDevice` / `ESPSimulated` into thin owners around `Playback`, decide
whether `ControllerDevice` / `SimDevice` are still earning their
indirection or whether `SimController` can talk to a `Playback` owner
directly. `debug_seek` was the historical reason for divergence and is
out of scope going forward.

## Mixed-timebase scene rejection

`controller/elemctl/service.py::_cmd_load_scene` merges strips from
multiple program entries into a single `CompiledManifest`. With
`Program::requires_sync` now a per-blob property, a scene could combine
synced and unsynced strips. Such a scene has no consistent meaning and
should be rejected at load time — likely in `_cmd_load_scene`, before
the combined manifest is built. Not a TODO yet because no concrete
scene-construction flow has hit this.

## Explicit "waiting on sync" state at the protocol level

The v3 controller_link adds an ACK to every command, including the
`Unsynced` rejection on synced START / RESUME / JUMP, so the original
"silent desync between controller and device" risk is gone. The open
trade-off is whether the protocol or controller UI should expose an
explicit "waiting on sync before play" state, or whether
controller-side `is_synced()` gating plus device-side `Unsynced` ACK
is sufficient. No action item — flag for revisit if multi-device
choreography surfaces UX gaps.
