"""The session layer: what each device should be doing, and whether it is.

The hub (hub.py) gives a dumb per-device pipe: it reports when a UID
connects or drops and carries bytes either way, but understands nothing
about command meaning or order. This layer fills that gap. It owns one
Member per configured device, tracks which are attached, and carries the
loaded program's identity (session_id, epoch) and the shared playback
anchor. drafts/controller_v3.md ("Sessions") is the spec.

Round A is the membership spine: the data model plus the connect /
disconnect / reboot lifecycle and the load() identity contract. Driving
devices with commands is the reconciler, added in later rounds; the
fields it needs (targets, op tokens, phase) exist here already so it
slots in without reshaping anything.
"""

import logging

from dataclasses import dataclass
from enum import Enum, auto

from .hub import DeviceConnected, DeviceDisconnected

log = logging.getLogger(__name__)


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


class Member:
    """One configured device's relationship to the current session.

    Holds the device's last confirmed phase, the program it should load,
    and the single in-flight operation. It never touches a socket; the
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
        self._op_seq = 0             # bumped to make any in-flight ACK stale
        self._inflight_op = None

    def on_connected(self, boot_token, rebooted):
        """A link came up for this device. The device dropped any program
        on its prior detach (src/app.cpp reset_for_detach), so its runtime
        starts unknown and the profile must be re-established."""
        self.attached = True
        self.boot_token = boot_token
        self.profile_state = ProfileState.UNKNOWN
        self._reset_runtime()
        self._bump_op()

    def on_disconnected(self, reason):
        """The link dropped. The session and this member's target survive;
        only the runtime is cleared. Idempotent: returns True only on the
        first call that actually detaches, so the session emits one event."""
        if not self.attached:
            return False
        self.attached = False
        self._reset_runtime()
        self._bump_op()
        return True

    def set_target(self, target):
        """Replace the desired end state and invalidate any in-flight op."""
        self._target = target
        self._bump_op()

    @property
    def target(self):
        return self._target

    def _reset_runtime(self):
        self.serving = False
        self.phase = DeviceState.UNKNOWN
        self.cursor_us = 0
        self._loaded_token = None

    def _bump_op(self):
        self._op_seq += 1
        self._inflight_op = None


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

        self.state = SessionState.IDLE
        self.session_id = 0          # 0 means no program loaded
        self.epoch = 0
        self.program_start_us = 0
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
        self.state = SessionState.LOADED

        for uid, member in self._members.items():
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

    def tick(self):
        """One loop iteration: drain the hub, apply lifecycle events, and
        return the session events that resulted."""
        poll = self._hub.poll(self._wanted_uids)
        for event in poll.events:
            self._apply_event(event)
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
