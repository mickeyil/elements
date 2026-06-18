"""Session tests against a fake hub.

The hub is replaced by a recorder: tick() drains whatever events the test
queued, so the membership lifecycle and the load() identity contract are
exercised without any sockets. Command sending (the reconciler) arrives in
a later round and brings its own send/ACK fake.
"""

import pytest

from elements.types import CompiledManifest, CompiledStripArtifact

from elemctl.config import DeviceConfig
from elemctl.hub import DeviceConnected, DeviceDisconnected, HubPoll
from elemctl.session import (
    Intent,
    MemberAttached,
    MemberDetached,
    ProfileState,
    Session,
    SessionState,
)


class FakeClock:
    def __init__(self):
        self.now_us = 1_000_000

    def __call__(self):
        return self.now_us


class FakeHub:
    """Records the wanted set and hands tick() whatever events were queued."""

    def __init__(self):
        self.pending_events = []
        self.frames = []
        self.wanted_seen = []

    def emit(self, *events):
        self.pending_events.extend(events)

    def poll(self, wanted_uids):
        self.wanted_seen.append(set(wanted_uids))
        events, self.pending_events = self.pending_events, []
        frames, self.frames = self.frames, []
        return HubPoll(events=events, frames=frames)


TOPOLOGY = [
    DeviceConfig(device_uid='sim-a', strip_id='left', length=30),
    DeviceConfig(device_uid='sim-b', strip_id='right', length=30),
]


def make_session(topology=TOPOLOGY):
    hub = FakeHub()
    session = Session(hub, topology, FakeClock())
    return session, hub


def make_manifest(*strips, duration=10.0):
    """strips: (strip_id, length, blob) tuples."""
    return CompiledManifest(
        duration=duration,
        strips=[
            CompiledStripArtifact(strip_id=sid, length=length, blob=blob)
            for sid, length, blob in strips
        ],
        safe_intervals=[(0.0, duration)],
    )


def tick(session):
    return session.tick(session._clock_us())


# ---------------------------------------------------------------------------
# Starting state
# ---------------------------------------------------------------------------

def test_members_start_unattached_and_idle():
    session, _hub = make_session()
    assert session.state is SessionState.IDLE
    assert session.session_id == 0
    assert session.attached_uids() == set()
    for uid in ('sim-a', 'sim-b'):
        assert session.member(uid).attached is False
        assert session.member(uid).serving is False


# ---------------------------------------------------------------------------
# Connect / disconnect lifecycle
# ---------------------------------------------------------------------------

def test_connect_attaches_member_and_emits_event():
    session, hub = make_session()
    hub.emit(DeviceConnected(uid='sim-a', boot_token=7, rebooted=False))

    events = tick(session)

    assert events == [MemberAttached(uid='sim-a', boot_token=7, rebooted=False)]
    assert session.is_attached('sim-a')
    assert session.member('sim-a').boot_token == 7
    assert session.attached_uids() == {'sim-a'}


def test_disconnect_detaches_and_emits_once():
    session, hub = make_session()
    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False))
    tick(session)

    hub.emit(DeviceDisconnected(uid='sim-a', reason='closed by device'))
    events = tick(session)
    assert events == [MemberDetached(uid='sim-a', reason='closed by device')]
    assert session.is_attached('sim-a') is False

    # A second disconnect for an already-detached member is a no-op.
    hub.emit(DeviceDisconnected(uid='sim-a', reason='closed by device'))
    assert tick(session) == []


def test_reconnect_after_disconnect_reattaches():
    session, hub = make_session()
    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False))
    hub.emit(DeviceDisconnected(uid='sim-a', reason='ack timeout'))
    tick(session)

    hub.emit(DeviceConnected(uid='sim-a', boot_token=2, rebooted=True))
    events = tick(session)

    assert events == [MemberAttached(uid='sim-a', boot_token=2, rebooted=True)]
    assert session.is_attached('sim-a')
    assert session.member('sim-a').boot_token == 2


def test_connect_always_resets_profile_to_unknown():
    session, hub = make_session()
    hub.emit(DeviceConnected(uid='sim-a', boot_token=5, rebooted=True))
    tick(session)
    # A profile is only ever ASSUMED_MATCH after our own SET_PROFILE Ok,
    # never assumed from a fresh connection.
    assert session.member('sim-a').profile_state is ProfileState.UNKNOWN


def test_unknown_uid_event_is_ignored():
    session, hub = make_session()
    hub.emit(DeviceConnected(uid='sim-ghost', boot_token=1, rebooted=False))
    assert tick(session) == []
    assert session.attached_uids() == set()


# ---------------------------------------------------------------------------
# Detach preserves session intent
# ---------------------------------------------------------------------------

def test_disconnect_clears_runtime_but_keeps_target():
    session, hub = make_session()
    session.load(make_manifest(('left', 30, b'L'), ('right', 30, b'R')))
    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False))
    tick(session)

    hub.emit(DeviceDisconnected(uid='sim-a', reason='ack timeout'))
    tick(session)

    member = session.member('sim-a')
    assert member.attached is False
    # The session still wants this member; only the runtime was cleared.
    assert member.target.intent is Intent.READY
    assert member.target.blob == b'L'


# ---------------------------------------------------------------------------
# load() identity and targeting contract
# ---------------------------------------------------------------------------

def test_load_sets_session_id_epoch_state_and_targets():
    session, _hub = make_session()
    session.load(make_manifest(('left', 30, b'L'), ('right', 30, b'R')))

    assert session.state is SessionState.LOADED
    assert session.session_id == 1
    assert session.epoch == 0

    left = session.member('sim-a').target
    right = session.member('sim-b').target
    assert left.intent is Intent.READY
    assert left.blob == b'L'
    assert left.strip_length == 30
    assert left.program_token == (1, 0)
    assert right.program_token == (1, 1)


def test_load_detaches_member_with_no_matching_strip():
    session, _hub = make_session()
    session.load(make_manifest(('left', 30, b'L')))   # nothing for 'right'

    assert session.member('sim-a').target.intent is Intent.READY
    assert session.member('sim-b').target.intent is Intent.DETACHED


def test_load_is_non_transactional_and_reassigns_each_time():
    session, _hub = make_session()
    session.epoch = 5   # a discontinuity bumped it during the prior session

    session.load(make_manifest(('left', 30, b'L'), ('right', 30, b'R')))
    assert session.session_id == 1
    assert session.epoch == 0
    first = session.member('sim-a').target.program_token

    session.load(make_manifest(('left', 30, b'L2'), ('right', 30, b'R2')))
    assert session.session_id == 2
    assert session.epoch == 0
    # A fresh session_id makes the program token distinct, so a stale-blob
    # device is seen as needing a reload.
    assert session.member('sim-a').target.program_token != first
    assert session.member('sim-a').target.blob == b'L2'


# ---------------------------------------------------------------------------
# tick plumbing
# ---------------------------------------------------------------------------

def test_tick_passes_wanted_uids_to_hub():
    session, hub = make_session()
    tick(session)
    assert hub.wanted_seen[-1] == {'sim-a', 'sim-b'}
