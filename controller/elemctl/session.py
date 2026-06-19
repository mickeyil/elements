"""The session layer: what each device should be doing, and whether it is.

The hub (hub.py) gives a dumb per-device pipe: it reports when a UID
connects or drops and carries bytes either way, but understands nothing
about command meaning or order. This layer fills that gap. It owns one
Member per configured device, tracks which are attached, and carries the
loaded program's identity (session_id, epoch) and the shared playback
anchor. drafts/controller_v3.md ("Sessions") is the spec.

Round A built the membership spine (the data model plus the connect /
disconnect / reboot lifecycle and the load() identity contract). Round B1
adds the reconciler: tick() drives each attached member one command at a
time from its confirmed phase toward its target, and the play() / stop()
verbs set those targets. Still to come in B2: pause / resume / seek and
rejoining a device mid-program (JUMP + RESUME onto a safe interval), plus
preview-frame assembly.
"""

import logging

from dataclasses import dataclass
from enum import Enum, auto

from . import wire
from .hub import DeviceConnected, DeviceDisconnected

log = logging.getLogger(__name__)

# How many ticks to wait before retrying a command the device refused with
# Unsynced; the device leases its clock over UDP on its own schedule, so we
# back off rather than retry every tick.
_UNSYNCED_BACKOFF_TICKS = 25

_US_PER_S = 1_000_000


class SessionState(Enum):
    IDLE = auto()
    LOADED = auto()
    PLAYING = auto()
    PAUSED = auto()
    ENDED = auto()


# The controller's mirror of the device's playback core. The first five
# values match the firmware DeviceState (src/playback.h); UNKNOWN is the
# controller-only addition, appended last, for a device whose state has
# not been confirmed since it connected.
class DeviceState(Enum):
    IDLE = auto()
    LOADED = auto()
    PLAYING = auto()
    PAUSED = auto()
    ENDED = auto()
    UNKNOWN = auto()


# What the session wants of a member, independent of where the device is.
# The reconciler closes the gap from phase to intent one command at a time.
class Intent(Enum):
    DETACHED = auto()   # not a participant in the current session
    READY = auto()      # profile applied and blob loaded, parked at LOADED
    PLAYING = auto()    # playing from the shared anchor
    PAUSED = auto()     # paused at a cursor


# A SET_PROFILE Ok means either "already matched" or "saved a new profile
# and is rebooting", indistinguishable on the wire (src/command_handler.cpp).
# So a confirmed profile is only ever ASSUMED_MATCH, and we always send
# SET_PROFILE first on a fresh connection.
class ProfileState(Enum):
    UNKNOWN = auto()
    ASSUMED_MATCH = auto()


@dataclass(frozen=True)
class Target:
    """The desired end state for one member. Replaced wholesale by the
    session verbs; a member reaches it command by command."""
    intent: Intent
    program_token: tuple = None      # (session_id, strip_id); None when DETACHED
    blob: bytes = None               # the compiled strip the device should load
    strip_length: int = 0
    anchor_us: int = 0               # program_start_us, for PLAYING
    cursor_us: int = 0               # paused position, for PAUSED


@dataclass(frozen=True)
class MemberAttached:
    uid: str
    boot_token: int
    rebooted: bool


@dataclass(frozen=True)
class MemberDetached:
    uid: str
    reason: str


@dataclass(frozen=True)
class MemberCommandFailed:
    """A command was refused with a terminal status. The member stops being
    driven until a new load, connect, or disconnect clears the block."""
    uid: str
    command: str
    status: int


class Member:
    """One configured device's relationship to the current session.

    Holds the device's last confirmed phase, the program it should load,
    and the single command in flight. It never touches a socket; the
    session sends on its behalf through the hub.
    """

    def __init__(self, uid, strip_id, strip_length):
        self.uid = uid
        self.strip_id = strip_id
        self.strip_length = strip_length

        self.attached = False
        self.serving = False
        self.boot_token = 0
        self.phase = DeviceState.UNKNOWN
        self.profile_state = ProfileState.UNKNOWN
        self.cursor_us = 0

        self._target = Target(intent=Intent.DETACHED)
        self._loaded_token = None    # program_token confirmed loaded on the device
        self._inflight = None        # name of the command awaiting an ACK, or None

        self.last_refusal = None     # (command, status) of the latest non-Ok ACK; cleared on Ok
        self.blocked = None          # (command, status) when a terminal failure halts driving
        self.retry_at_tick = 0       # earliest tick to retry after an Unsynced refusal
        self.start_authorized = False  # may issue the initial Start (the play() cohort)

    def on_connected(self, boot_token, rebooted):
        """A link came up for this device. The device dropped any program
        on its prior detach (src/app.cpp reset_for_detach), so its runtime
        starts unknown and the profile must be re-established."""
        self.attached = True
        self.boot_token = boot_token
        self.profile_state = ProfileState.UNKNOWN
        self._reset_runtime()
        self._clear_blocked()   # a fresh device clears any prior terminal block

    def on_disconnected(self, reason):
        """The link dropped. The session and this member's target survive;
        only the runtime is cleared. Idempotent: returns True only on the
        first call that actually detaches, so the session emits one event."""
        if not self.attached:
            return False
        self.attached = False
        self._reset_runtime()
        self._clear_blocked()
        return True

    def set_target(self, target):
        """Replace the desired end state. A command already in flight is left
        to complete: its ACK reflects the real device transition, and the
        reconciler then drives toward the new target. Only the transient
        intent flags reset."""
        self._target = target
        self.start_authorized = False
        self.retry_at_tick = 0
        self.last_refusal = None

    @property
    def target(self):
        return self._target

    def _reset_runtime(self):
        # A new link: the device's runtime starts unknown and any command that
        # was in flight on the old link is gone with it.
        self.serving = False
        self.phase = DeviceState.UNKNOWN
        self.cursor_us = 0
        self._loaded_token = None
        self._inflight = None
        self.start_authorized = False
        self.retry_at_tick = 0
        self.last_refusal = None

    def _clear_blocked(self):
        self.blocked = None


class Session:
    """One program across all configured devices, surviving device detach.

    Construct with the hub, the device configs, and the monotonic
    microsecond clock the hub's sync server uses (so the anchor this layer
    stamps matches the clock devices sync against). Call tick() once per
    loop; it drains the hub and returns session events.
    """

    def __init__(self, hub, device_configs, clock_us):
        self._hub = hub
        self._clock_us = clock_us
        self._members = {
            dc.device_uid: Member(dc.device_uid, dc.strip_id, dc.length)
            for dc in device_configs
        }
        self._wanted_uids = set(self._members)
        self._events = []
        self._tick_count = 0

        self.state = SessionState.IDLE
        self.session_id = 0          # 0 means no program loaded
        self.epoch = 0
        self.program_start_us = 0
        self.cursor_us = 0           # paused program position, in microseconds
        self.manifest = None

    def load(self, manifest):
        """Commit a new desired session: a fresh session_id, a reset epoch, and
        every member retargeted to its compiled strip. The routing is checked
        first, so an unroutable load (a strip no device serves, or one compiled
        for more pixels than the device has) raises before any session state
        changes and is therefore a no-op. Commitment is not transactional only
        afterward: a member that later fails its join becomes an errored
        non-serving member rather than rolling back.

        Routing is by strip_id: each device loads the manifest strip whose
        strip_id matches its own, and several devices may share a strip_id and
        all load that one strip (mirroring). A device whose strip_id is absent
        from the manifest detaches. strip_id is unique per manifest (the
        compiler enforces it), so the match is always unambiguous."""
        self._check_all_strips_served(manifest)
        self._check_geometry(manifest)

        self.manifest = manifest
        self.session_id += 1
        self.epoch = 0
        self.program_start_us = 0
        self.cursor_us = 0
        self.state = SessionState.LOADED

        for uid, member in self._members.items():
            member._clear_blocked()   # a new program is a fresh chance to drive
            artifact = manifest.strips.get(member.strip_id)
            if artifact is None:
                member.set_target(Target(intent=Intent.DETACHED))
                continue
            member.set_target(Target(
                intent=Intent.READY,
                program_token=(self.session_id, member.strip_id),
                blob=artifact.blob,
                strip_length=artifact.length,
            ))

    def _check_all_strips_served(self, manifest):
        """Every manifest strip must be served by at least one device. A strip
        whose strip_id no configured device carries is a routing mistake (a
        typo'd strip_id or a device missing from the config), not a strip to
        silently drop."""
        served = {member.strip_id for member in self._members.values()}
        for strip_id in manifest.strips:
            if strip_id not in served:
                raise ValueError(f'no configured device serves strip_id {strip_id!r}')

    def _check_geometry(self, manifest):
        """Reject a strip compiled for more pixels than the device physically
        has; a shorter strip is allowed, since the DSL permits an authored
        length up to (not only equal to) the configured one.

        Round B note: this assumes SET_PROFILE will adopt the blob's own
        length, so the device decoder's exact strip_length match still holds
        for a shorter blob. If SET_PROFILE instead sends the configured length,
        a shorter blob would fail on the device, and the rule here (and in the
        DSL) must tighten to an exact match."""
        for member in self._members.values():
            artifact = manifest.strips.get(member.strip_id)
            if artifact is not None and artifact.length > member.strip_length:
                raise ValueError(
                    f'device {member.uid!r} ({member.strip_length} px) cannot '
                    f'serve strip_id {member.strip_id!r} compiled for '
                    f'{artifact.length} px'
                )

    def play(self):
        """Begin playback from a single shared anchor stamped now.

        Every routed member is retargeted to PLAYING (so a late joiner carries
        the right desired intent for B2 rejoin), but only members already
        LOADED with the right program are authorized to issue the initial
        Start. Members still loading, or attaching later, park at LOADED until
        B2 can rejoin them mid-program safely.

        Valid from a loaded, stopped session or a naturally ENDED one (replay
        from 0). From PLAYING/PAUSED the operator resumes or stops first, so a
        re-stamped anchor can never drift devices that are already playing or
        lose a paused cursor. Replay still needs playback settled: a member
        left paused (e.g. a Resume the device refused) is stopped first, so
        replay always restarts from 0 rather than resuming a stale cursor."""
        if self.state not in (SessionState.LOADED, SessionState.ENDED):
            raise ValueError('play() is only valid from a loaded or ended session')
        if self._has_unsettled_playback():
            raise ValueError('play() requires playback to be stopped first')
        self.program_start_us = self._clock_us()
        self.cursor_us = 0
        self.state = SessionState.PLAYING
        for member in self._members.values():
            current = member.target
            if current.intent is Intent.DETACHED:
                continue
            # Authorize on the loaded program, not the instantaneous phase: a
            # member mid-transition (e.g. a stop just sent) still has the right
            # blob and should start once the reconciler resyncs it to LOADED.
            ready = member._loaded_token == current.program_token
            member.set_target(Target(
                intent=Intent.PLAYING,
                program_token=current.program_token,
                blob=current.blob,
                strip_length=current.strip_length,
                anchor_us=self.program_start_us,
            ))
            member.start_authorized = ready   # set after set_target (which clears it)

    def stop(self):
        """Return every routed member to a parked LOADED state."""
        if self.state is SessionState.IDLE:
            raise ValueError('stop() requires a loaded program')
        self.state = SessionState.LOADED
        self.cursor_us = 0
        self._retarget_routed(Intent.READY)

    def pause(self):
        """Freeze playback at the current program cursor. Resume keeps device
        engine state intact (no rebuild), so pausing needs no safe interval."""
        if self.state is not SessionState.PLAYING:
            raise ValueError('pause() requires a playing session')
        if self._has_pending_start_or_resume():
            raise ValueError('pause() requires playback to settle first')
        self.cursor_us = self._clock_us() - self.program_start_us
        self.state = SessionState.PAUSED
        self._retarget_routed(Intent.PAUSED, anchor_us=self.program_start_us,
                              cursor_us=self.cursor_us)

    def resume(self):
        """Resume playback from the paused cursor by re-anchoring program time
        so it continues where it stopped."""
        if self.state is not SessionState.PAUSED:
            raise ValueError('resume() requires a paused session')
        if self._has_unsettled_pause():
            raise ValueError('resume() requires pause to settle first')
        self.program_start_us = self._clock_us() - self.cursor_us
        self.state = SessionState.PLAYING
        self._retarget_routed(Intent.PLAYING, anchor_us=self.program_start_us,
                              cursor_us=self.cursor_us)

    def _has_unsettled_playback(self):
        """True when a routed device is playing (or about to) and not already
        being stopped. A fresh play() then would restamp the shared anchor
        while that device keeps running on the old one. Restart-while-playing
        is B2; until then play() is refused in this state. A stop already in
        flight is settling, so it does not count."""
        for member in self._members.values():
            if member.target.intent is Intent.DETACHED:
                continue
            if member._inflight == 'start':
                return True
            if (member.phase in (DeviceState.PLAYING, DeviceState.PAUSED)
                    and member._inflight != 'stop'):
                return True
        return False

    def _has_unsettled_pause(self):
        """True when a routed member is still playing or transitioning toward
        paused (a Pause or Start in flight). resume() must wait for pause to
        settle, or it would re-anchor program time against a device that never
        actually paused and is still running on the old anchor."""
        for member in self._members.values():
            if member.target.intent is Intent.DETACHED:
                continue
            if member._inflight in ('start', 'pause'):
                return True
            if member.phase is DeviceState.PLAYING:
                return True
        return False

    def _has_pending_start_or_resume(self):
        """True when a routed member is mid initial-start or mid-resume: the
        play cohort has not started yet (start_authorized, which stays set
        until the Start ACK), a Start/Resume is in flight, or it is paused
        while the target wants PLAYING. pause() must wait for this to settle,
        or it would clear a cohort's start authorization (stranding the device,
        since B2a has no rejoin to recover it) or capture a cursor against an
        anchor the device has not adopted. Parked late members (start_authorized
        False, nothing in flight) are not caught, so pause() of the playing
        devices is not blocked by them."""
        for member in self._members.values():
            if member.target.intent is Intent.DETACHED:
                continue
            if member._inflight in ('start', 'resume'):
                return True   # in flight (first attempt or retry): wait for the ACK
            if member.start_authorized:
                # A surfaced *start* refusal (e.g. a device that cannot sync)
                # releases the operator to pause the rest of the show; an
                # in-flight retry is already caught above. A resume refusal does
                # not release here (the device is still paused, caught below).
                if member.last_refusal is not None and member.last_refusal[0] == 'start':
                    continue
                return True
            if (member.phase is DeviceState.PAUSED
                    and member.target.intent is Intent.PLAYING):
                return True
        return False

    def _retarget_routed(self, intent, anchor_us=0, cursor_us=0):
        """Replace the intent on every non-detached member, carrying its
        program identity and blob forward. Detached members keep DETACHED."""
        for member in self._members.values():
            current = member.target
            if current.intent is Intent.DETACHED:
                continue
            member.set_target(Target(
                intent=intent,
                program_token=current.program_token,
                blob=current.blob,
                strip_length=current.strip_length,
                anchor_us=anchor_us,
                cursor_us=cursor_us,
            ))

    def tick(self):
        """One loop iteration: drain the hub, apply lifecycle events, drive
        each member toward its target, and return the session events."""
        self._tick_count += 1
        poll = self._hub.poll(self._wanted_uids)
        for event in poll.events:
            self._apply_event(event)
        self._check_end()
        self._reconcile()
        # Preview frames are handled in a later round.
        return self._drain_events()

    def member(self, uid):
        return self._members.get(uid)

    def is_attached(self, uid):
        member = self._members.get(uid)
        return member is not None and member.attached

    def attached_uids(self):
        return {uid for uid, m in self._members.items() if m.attached}

    def _apply_event(self, event):
        member = self._members.get(event.uid)
        if member is None:
            # The hub only surfaces wanted UIDs; ignore anything else.
            return
        if isinstance(event, DeviceConnected):
            member.on_connected(event.boot_token, event.rebooted)
            self._emit(MemberAttached(
                uid=event.uid,
                boot_token=event.boot_token,
                rebooted=event.rebooted,
            ))
            log.info('session: %s attached (boot_token=%d%s)',
                     event.uid, event.boot_token,
                     ', rebooted' if event.rebooted else '')
        elif isinstance(event, DeviceDisconnected):
            if member.on_disconnected(event.reason):
                self._emit(MemberDetached(uid=event.uid, reason=event.reason))
                log.info('session: %s detached: %s', event.uid, event.reason)

    def _emit(self, event):
        self._events.append(event)

    def _drain_events(self):
        events = self._events
        self._events = []
        return events

    # -----------------------------------------------------------------------
    # Reconciler: move each attached member one command toward its target.
    # -----------------------------------------------------------------------

    def _reconcile(self):
        for member in self._members.values():
            if not member.attached:
                continue
            if member._inflight is not None or member.blocked is not None:
                continue
            if member.retry_at_tick > self._tick_count:
                continue
            self._reconcile_member(member)

    def _reconcile_member(self, member):
        """Issue at most one command to close the gap from the member's
        confirmed phase to its target. Profile first on a fresh connection,
        then the right blob, then the playback verb."""
        target = member.target

        # A member the manifest no longer routes to: blank a stale program from
        # a previous session once, then leave it idle. No SET_PROFILE(0).
        if target.intent is Intent.DETACHED:
            if member.serving:
                self._send(member, 'stop', wire.encode_stop(),
                           lambda m: self._after_stop_detached(m))
            return

        if member.profile_state is ProfileState.UNKNOWN:
            self._send(member, 'set_profile',
                       wire.encode_set_profile(target.strip_length),
                       lambda m: self._after_set_profile(m))
            return

        if member._loaded_token != target.program_token:
            token = target.program_token
            self._send(member, 'load', wire.encode_load(target.blob),
                       lambda m: self._after_load(m, target),
                       still_relevant=lambda m: m.target.program_token == token)
            return

        if target.intent is Intent.READY:
            if member.phase in (DeviceState.PLAYING, DeviceState.PAUSED):
                self._send(member, 'stop', wire.encode_stop(),
                           lambda m: self._after_stop(m))
            return

        if target.intent is Intent.PLAYING:
            # Don't drive playback unless the session is actually playing. After
            # a natural end the targets stay PLAYING, but a member that still
            # carries start authorization (e.g. a Start refused Unsynced, never
            # cleared) would otherwise be Started into an ended session. play()
            # re-arms the session before any replay; profile and load above
            # still run, so a device attaching while ended can prepare.
            if self.state is not SessionState.PLAYING:
                return
            # The authorized cohort starts from program time 0. An unauthorized
            # PLAYING member at LOADED (a late joiner / reconnector) parks until
            # B2b's JUMP + RESUME rejoin can place it safely. ENDED is allowed
            # so replay Starts without a reload (the device permits Start from
            # ENDED and resets its engine).
            if (member.phase in (DeviceState.LOADED, DeviceState.ENDED)
                    and member.start_authorized):
                self._send(member, 'start', wire.encode_start(target.anchor_us),
                           lambda m: self._after_start(m),
                           still_relevant=lambda m: m.target.intent is Intent.PLAYING)
            elif member.phase is DeviceState.PAUSED and self._reached(target.cursor_us):
                # Resume only once live time has reached the cursor: unsynced
                # devices ignore the anchor and resume immediately, so the
                # controller must hold Resume until then (a no-op wait for an
                # operator resume, which re-anchors to the cursor).
                self._send(member, 'resume', wire.encode_resume(target.anchor_us),
                           lambda m: self._after_resume(m),
                           still_relevant=lambda m: m.target.intent is Intent.PLAYING)
            return

        if target.intent is Intent.PAUSED:
            if member.phase is DeviceState.PLAYING:
                self._send(member, 'pause', wire.encode_pause(),
                           lambda m: self._after_pause(m))
            # phase PAUSED is satisfied; phase LOADED (a reloaded device) is
            # rejoined to its cursor in B2b, where the cursor's safety is checked.
            return

    def _send(self, member, command, encoded, apply_ok, still_relevant=None):
        """Dispatch one command and register its ACK handler. Only one command
        is in flight per member at a time.

        An Ok ACK is always applied: it reports a transition the device really
        made, and the reconciler resolves any retarget that landed meanwhile on
        the next tick. A non-Ok ACK is acted on only when still_relevant(member)
        holds, so a refusal from a command the current target no longer wants
        (e.g. an old Load failing after the operator loaded a different program)
        cannot block or delay the new target. Relevance is semantic per command
        (the same blob, the same intent), not target-object identity, so an
        intent-only retarget on the same program still surfaces its failure."""
        member._inflight = command

        def on_ack(ack):
            member._inflight = None
            if ack.status == wire.ACK_OK:
                member.last_refusal = None
                apply_ok(member)
            elif still_relevant is None or still_relevant(member):
                self._handle_nonok(member, command, ack.status)

        self._hub.send(member.uid, encoded, on_ack)

    def _handle_nonok(self, member, command, status):
        member.last_refusal = (command, status)
        if status == wire.ACK_UNSYNCED:
            # Recoverable: the device will lease its clock and accept later.
            member.retry_at_tick = self._tick_count + _UNSYNCED_BACKOFF_TICKS
        elif status == wire.ACK_PROFILE_MISMATCH:
            # The blob's geometry did not match the active profile; re-profile.
            member.profile_state = ProfileState.UNKNOWN
            self._invalidate_load(member)
        elif status == wire.ACK_WRONG_STATE:
            if command == 'load':
                # LOAD WrongState means no hardware profile (command_handler.cpp).
                member.profile_state = ProfileState.UNKNOWN
            # Either way our phase model drifted; force a reload.
            self._invalidate_load(member)
        else:
            # ACK_ERROR / ACK_BAD_PAYLOAD: terminal. Stop driving and surface it.
            member.blocked = (command, status)
            self._emit(MemberCommandFailed(uid=member.uid, command=command,
                                           status=status))

    def _invalidate_load(self, member):
        """Force the ladder back to LOAD: drop the confirmed program and the
        start authorization so a stale phase can't trigger an unsafe Start."""
        member._loaded_token = None
        member.phase = DeviceState.UNKNOWN
        member.start_authorized = False

    def _after_set_profile(self, member):
        # SET_PROFILE Ok means "already matched" or "saved and rebooting",
        # indistinguishable; a real reboot drops the link and resets us.
        member.profile_state = ProfileState.ASSUMED_MATCH

    def _after_load(self, member, target):
        member.phase = DeviceState.LOADED
        member._loaded_token = target.program_token
        member.serving = True

    def _after_stop(self, member):
        # STOP ACKs Ok unconditionally; only treat it as LOADED when we knew a
        # program was loaded, so an idle device is not mistaken for LOADED.
        if member._loaded_token is not None:
            member.phase = DeviceState.LOADED

    def _after_stop_detached(self, member):
        self._after_stop(member)   # the blank Stop also returns it to LOADED
        member.serving = False

    def _after_start(self, member):
        member.start_authorized = False   # the initial Start is consumed
        member.phase = self._playing_phase()

    def _after_pause(self, member):
        member.phase = DeviceState.PAUSED

    def _after_resume(self, member):
        member.phase = self._playing_phase()

    def _playing_phase(self):
        """The phase a Start or Resume Ok records. Normally PLAYING; but a
        Start/Resume that lands after the session already ended has an anchor
        past the duration, so the device ends on its next render. Mirror that
        rather than recording a PLAYING that never really happened."""
        return (DeviceState.ENDED if self.state is SessionState.ENDED
                else DeviceState.PLAYING)

    def _reached(self, cursor_us):
        """True once live program time has reached cursor_us (microseconds)."""
        return self._clock_us() - self.program_start_us >= cursor_us

    def _duration_us(self):
        return int(self.manifest.duration * _US_PER_S)

    def _check_end(self):
        """Natural end of program. The device transitions PLAYING -> ENDED when
        its render passes the program duration (src/playback.cpp); there is no
        end event or query, so the controller ends the session by the same
        clock the devices follow: once live program time reaches the duration,
        the session and every playing member are ENDED.

        Only runs while PLAYING, the one state with a live anchor (when paused
        or stopped, now - program_start_us keeps growing against a stale anchor
        with playback frozen). A member still at LOADED never started and is
        left untouched. For unsynced programs the device anchors on its own
        receive time, so this can read ENDED a delivery delay early; that is the
        accepted clock approximation, and the PLAYING-rung guard keeps it from
        issuing any stray command."""
        if self.state is not SessionState.PLAYING:
            return
        if self._clock_us() - self.program_start_us < self._duration_us():
            return
        self.state = SessionState.ENDED
        for member in self._members.values():
            if member.target.intent is Intent.DETACHED:
                continue
            if member.phase is DeviceState.PLAYING:
                member.phase = DeviceState.ENDED
