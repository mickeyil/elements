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


# What the controller believes the device's playback core is doing. It
# mirrors the firmware DeviceState (src/playback.h); UNKNOWN is the extra
# controller-side value for a device whose state has not been confirmed
# since it connected.
class MemberPhase(Enum):
    UNKNOWN = auto()
    IDLE = auto()
    LOADED = auto()
    PLAYING = auto()
    PAUSED = auto()
    ENDED = auto()


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
    intent: object                   # Intent
    program_token: object = None     # (session_id, slot_index); None when DETACHED
    blob: object = None              # the compiled strip the device should load
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
        self.phase = MemberPhase.UNKNOWN
        self.profile_state = ProfileState.UNKNOWN
        self.cursor_us = 0
        self.error = None

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
        self.error = reason
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
        self.phase = MemberPhase.UNKNOWN
        self.cursor_us = 0
        self._loaded_token = None

    def _bump_op(self):
        self._op_seq += 1
        self._inflight_op = None


class Session:
    """One program across all configured devices, surviving device detach.

    Construct with the hub, the device topology, and the monotonic
    microsecond clock the hub's sync server uses (so the anchor this layer
    stamps matches the clock devices sync against). Call tick() once per
    loop; it drains the hub and returns session events.
    """

    def __init__(self, hub, topology, clock_us):
        self._hub = hub
        self._clock_us = clock_us
        self._members = {
            dc.device_uid: Member(dc.device_uid, dc.strip_id, dc.length)
            for dc in topology
        }
        self._wanted_uids = set(self._members)
        self._events = []

        self.state = SessionState.IDLE
        self.session_id = 0          # 0 means no program loaded
        self.epoch = 0
        self.program_start_us = 0
        self.manifest = None

    def load(self, manifest):
        """Commit a new desired session immediately: a fresh session_id, a
        reset epoch, and every member retargeted to its compiled strip. This
        is not transactional; a member that later fails its join becomes an
        errored non-serving member rather than rolling back the session."""
        self.manifest = manifest
        self.session_id += 1
        self.epoch = 0
        self.program_start_us = 0
        self.state = SessionState.LOADED

        slot_by_strip = {}
        for slot_index, strip in enumerate(manifest.strips):
            slot_by_strip.setdefault(strip.strip_id, (slot_index, strip))

        for member in self._members.values():
            assignment = slot_by_strip.get(member.strip_id)
            if assignment is None:
                member.set_target(Target(intent=Intent.DETACHED))
                continue
            slot_index, strip = assignment
            member.set_target(Target(
                intent=Intent.READY,
                program_token=(self.session_id, slot_index),
                blob=strip.blob,
                strip_length=strip.length,
            ))

    def tick(self, now_us):
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
