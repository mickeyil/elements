# JUMP and live rejoin (deferred)

The design for two device capabilities the controller does not yet
use: operator seek, and live rejoin of a device that joins or rejoins
a program after it has already started. The device runtime and the
compiler implement their halves of both; only the controller's use of
them is deferred, to keep the first working version small. This doc
records the reasoning so it is not lost, and so adding the feature
later is a controller-only task. The device behavior is the source of
truth in `src/playback.cpp` and `src/command_handler.cpp`; the
interval analysis is in `compiler/elements/compiler.py`.

## Background

A freshly loaded program is only safe to render from program time 0.
Building the engine and then rendering at some later time skips the
frames in between, and any animation whose output depends on an
earlier event's output renders wrong. So a device that did not start
with the others (it powered on late, or it dropped the link and had to
reload) cannot just `START`: an unsynced program would restart from 0,
and a synced `START` against the shared anchor would render at live
time with no safe-interval admission, so its output is wrong. `RESUME`
only continues a real pause, so it is no help either. The device has
to be repositioned onto a point the compiler has certified as safe to
begin from cold, and that reposition is `JUMP`.

## What the device already does

`Playback::handle_jump` (`src/playback.cpp`) accepts a program time
from `LOADED` or `PAUSED`, requires it to be strictly ahead of the
current cursor and inside the program, and for a synced program
requires the clock lease to be active. It resets the engine, moves the
cursor to that time, transitions to `PAUSED`, and renders nothing.

The behavior that makes rejoin work is what follows. After a `JUMP`,
a `RESUME` against the shared anchor leaves the device's render
holding until the shared clock reaches the jumped cursor, then renders
forward from there. So jumping a little ahead of live time makes the
device render cleanly at the safe point the moment the clock arrives,
rather than at live time plus a round trip. This half is built and
tested; keeping it costs nothing.

## Safe intervals

The compiler computes, per program, the time ranges in which
rebuilding engine state from scratch and rendering is correct. The
ranges are half-open; intersected across all of a program's strips, so
one target is safe for every device at once; and filtered to drop any
range narrower than one frame period, since such a range cannot render
even one frame before the next write lands. A zero-width `(0, 0)`
marker is kept at the front to record that `START` at program time 0
is always safe. The controller, not the compiler, leaves one frame
period of headroom before a range's end when it picks a target.

The computation lives in `compiler/elements/compiler.py`
(`_find_safe_intervals` and the intersection and width filter that
follow it) and rides on `CompiledManifest.safe_intervals`. It is the
part most worth preserving: the dependency-chain reasoning behind it
is hard to reconstruct, while the wire encoder and the device handler
are small. The compiler keeps emitting it whether or not the
controller consumes it.

## The rejoin sequences

To rejoin a device into a playing program the controller sends `LOAD`,
then `JUMP` to a safe point at or just ahead of the live cursor, then
`RESUME` against the shared anchor; the device waits at the safe point
until the clock reaches it and then follows the shared timeline. To
rejoin into a paused program it sends `LOAD` and `JUMP` to the safe
point and stops there, with no `RESUME`; the device waits dark at that
point and, when the operator resumes, renders once program time
reaches the jumped cursor. If no safe interval is reachable from the
current cursor, the device cannot be rejoined in that segment and is
left as it is. Operator seek is the same machinery aimed at a point
the operator chose rather than the live cursor, snapped to the nearest
safe interval; because `JUMP` only moves forward of the current
cursor, a backward seek must reload, resetting the cursor to 0, before
jumping.

## What the controller would add

The reconciler today brings a late or reconnecting member to `LOADED`
and parks it, because it is not part of the started group (its start
authorization is unset). Two branches of the reconciler would change
to rejoin instead of park: the playing branch would `JUMP` such a
member onto a safe point and then `RESUME` it, and the paused branch
would `JUMP` it to the paused cursor's safe point and leave it. The
supporting pieces are a helper that picks the safe target with
headroom, a per-member flag that tells a post-`JUMP` resume apart from
an operator resume, and a `JUMP` acknowledgement policy that mirrors
`START` (a not-synced refusal backs off and retries, a wrong-state
reply forces a reload, an out-of-range time is a controller or
compiler bug and stops the member). Seek adds an operator verb that
chooses the target. None of this is built; the parked-member branches
in `controller/elemctl/session.py` mark where it would attach.

## What the first version does instead, and what it costs

The controller never issues `JUMP`. A device that joins or rejoins
after the program started loads its blob and then waits at `LOADED`,
dark, for the rest of the run; a `stop` followed by `play` restarts
the whole set from 0 and brings it back in. The costs are direct: a
device that drops mid-program does not catch up, a device that
connects late misses the current run, and a device that reconnects
into a paused program cannot show the frozen frame and can only wait.
There is no seek.

In exchange the rest of the system stays smaller. The playback
commands are only `SetProfile`, `Load`, `Start`, `Pause`, `Resume`,
and `Stop`. `Start` always means start the initial group from 0, and
`Resume` always means continue a real pause, never reconstruct state.
Preview-frame assembly, when it lands, clears its buckets only on
`Load`, with no mid-stream reset on `JUMP` to reason about. The
operator service introduces no rejoin event and no safe-interval
display.

One caution for whoever simplifies further: the start-authorization
flag and the parking it drives are not rejoin scaffolding. They are
what stops a device that missed the start from playing from 0 and
running out of sync with the others. They stay.

## What stays in place

`CMD_JUMP` and `handle_jump` in the device, `encode_jump` in the wire
codecs, and the safe-interval computation in the compiler are all
kept, dormant. They are built and tested; removing them would churn
the device and compiler test suites for no runtime gain, and leaving
them means reintroducing live rejoin or seek later is purely
controller work, starting from this document.
