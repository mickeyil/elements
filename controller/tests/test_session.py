"""Session tests against a fake hub.

The hub is replaced by a recorder: tick() drains whatever events the test
queued and records the commands the reconciler sends, so the membership
lifecycle, the load() identity contract, and the reconciler (its command
ladder and ACK handling) are all exercised without any sockets. Tests
invoke the recorded ACK callbacks to simulate device replies.
"""

import pytest

from elements.types import CompiledManifest, CompiledStripArtifact

from elemctl import wire
from elemctl.config import DeviceConfig
from elemctl.hub import DeviceConnected, DeviceDisconnected, HubPoll
from elemctl.wire import AckMsg, FramePreview
from elemctl.session import (
    DeviceState,
    Intent,
    MemberAttached,
    MemberCommandFailed,
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
        self.sent = []   # (uid, encoded, on_ack) per reconciler command
        self.disconnected = []   # (uid, reason) per disconnect() call

    def emit(self, *events):
        self.pending_events.extend(events)

    def disconnect(self, uid, reason='removed'):
        self.disconnected.append((uid, reason))

    def poll(self, wanted_uids):
        self.wanted_seen.append(set(wanted_uids))
        events, self.pending_events = self.pending_events, []
        frames, self.frames = self.frames, []
        return HubPoll(events=events, frames=frames)

    def send(self, uid, encoded, on_ack=None):
        self.sent.append((uid, encoded, on_ack))
        return True

    def last_send(self, uid=None):
        """The most recent (opcode, on_ack) sent, optionally filtered by uid."""
        for u, encoded, on_ack in reversed(self.sent):
            if uid is None or u == uid:
                return encoded[4], on_ack   # encoded[4] is the opcode byte
        return None, None


def ack_ok():
    return AckMsg(status=wire.ACK_OK, payload=b'')


def ack(status):
    return AckMsg(status=status, payload=b'')


DISTINCT_STRIPS = [
    DeviceConfig(device_uid='sim-a', strip_id='left', length=30),
    DeviceConfig(device_uid='sim-b', strip_id='right', length=30),
]

# Two devices sharing one strip_id (legal when lengths match): both load the
# same compiled strip, the mirroring case (e.g. an esp and a sim side by side).
SHARED_STRIP = [
    DeviceConfig(device_uid='sim-a', strip_id='wall', length=30),
    DeviceConfig(device_uid='sim-b', strip_id='wall', length=30),
]

SINGLE = [DeviceConfig(device_uid='sim-a', strip_id='main', length=30)]

# sim-a serves 'main'; sim-b serves 'side'. A manifest with only 'main'
# detaches sim-b while staying fully served.
TWO_STRIPS = [
    DeviceConfig(device_uid='sim-a', strip_id='main', length=30),
    DeviceConfig(device_uid='sim-b', strip_id='side', length=30),
]


def make_session(device_configs=DISTINCT_STRIPS):
    hub = FakeHub()
    session = Session(hub, device_configs, FakeClock())
    return session, hub


def make_manifest(*strips, duration=10.0):
    """strips: (strip_id, length, blob) tuples; strip_id is unique per manifest."""
    return CompiledManifest(
        duration=duration,
        strips={
            sid: CompiledStripArtifact(strip_id=sid, length=length, blob=blob)
            for sid, length, blob in strips
        },
        safe_intervals=[(0.0, duration)],
    )


def tick(session):
    return session.tick()


def drive_to_loaded(session, hub, uid='sim-a', blob=b'M'):
    """Load a single-strip manifest, attach the device, and ACK its way to a
    parked LOADED state (SET_PROFILE Ok, then LOAD Ok)."""
    session.load(make_manifest(('main', 30, blob)))
    hub.emit(DeviceConnected(uid=uid, boot_token=1, rebooted=False))
    tick(session)                       # attach + SET_PROFILE
    _, on_ack = hub.last_send(uid)
    on_ack(ack_ok())                    # profile accepted
    tick(session)                       # LOAD
    _, on_ack = hub.last_send(uid)
    on_ack(ack_ok())                    # blob loaded; member parked at LOADED


def drive_to_playing(session, hub, uid='sim-a'):
    """Bring the device to LOADED, then play() and ACK the Start: PLAYING."""
    drive_to_loaded(session, hub, uid=uid)
    session.play()
    tick(session)
    _, on_ack = hub.last_send(uid)
    on_ack(ack_ok())


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
# Device editing (add / edit / remove, gated to a pre-load session)
# ---------------------------------------------------------------------------

def test_can_edit_devices_only_before_a_program_is_loaded():
    session, hub = make_session(SINGLE)
    assert session.can_edit_devices() is True
    drive_to_loaded(session, hub)
    assert session.can_edit_devices() is False


def test_add_device_joins_membership_and_wanted_set():
    session, hub = make_session(SINGLE)
    session.add_device(DeviceConfig(device_uid='sim-b', strip_id='side', length=30))
    assert session.member('sim-b') is not None
    tick(session)
    assert 'sim-b' in hub.wanted_seen[-1]


def test_added_device_routes_on_the_next_load():
    session, _hub = make_session(SINGLE)
    session.add_device(DeviceConfig(device_uid='sim-b', strip_id='side', length=30))
    session.load(make_manifest(('main', 30, b'M'), ('side', 30, b'S')))
    assert session.member('sim-b').target.intent is Intent.READY


def test_add_device_allowed_after_load_parks_detached():
    # Adding a device with a program already loaded is allowed: the new member
    # parks DETACHED, so it does not join or disturb the running program.
    session, hub = make_session(SINGLE)
    drive_to_loaded(session, hub)
    assert session.can_edit_devices() is False   # edit/remove stay gated
    session.add_device(DeviceConfig(device_uid='sim-b', strip_id='side', length=30))
    assert session.member('sim-b') is not None
    assert session.member('sim-b').target.intent is Intent.DETACHED


def test_device_added_after_load_routes_on_the_next_load():
    session, hub = make_session(SINGLE)
    drive_to_loaded(session, hub)
    session.add_device(DeviceConfig(device_uid='sim-b', strip_id='side', length=30))
    session.load(make_manifest(('main', 30, b'M'), ('side', 30, b'S')))
    assert session.member('sim-b').target.intent is Intent.READY


def test_edit_and_remove_rejected_after_load():
    session, hub = make_session(SINGLE)
    drive_to_loaded(session, hub)
    with pytest.raises(ValueError, match='only be edited before a program'):
        session.edit_device(
            'sim-a', DeviceConfig(device_uid='sim-a', strip_id='main', length=60))
    with pytest.raises(ValueError, match='only be edited before a program'):
        session.remove_device('sim-a')


def test_edit_device_updates_routing_fields_in_place():
    session, _hub = make_session(SINGLE)
    session.edit_device('sim-a',
                        DeviceConfig(device_uid='sim-a', strip_id='main', length=60))
    member = session.member('sim-a')
    assert member.strip_length == 60 and member.strip_id == 'main'


def test_edit_device_uid_change_is_remove_then_add():
    session, hub = make_session(SINGLE)
    session.edit_device('sim-a',
                        DeviceConfig(device_uid='sim-b', strip_id='main', length=30))
    assert session.member('sim-a') is None
    assert ('sim-a', 'removed') in hub.disconnected
    assert session.member('sim-b') is not None


def test_remove_device_disconnects_and_drops():
    session, hub = make_session()   # DISTINCT_STRIPS: sim-a, sim-b
    session.remove_device('sim-b')
    assert session.member('sim-b') is None
    assert ('sim-b', 'removed') in hub.disconnected
    tick(session)
    assert 'sim-b' not in hub.wanted_seen[-1]


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
    assert left.program_token == (1, 'left')
    assert right.program_token == (1, 'right')


def test_load_detaches_member_with_no_matching_strip():
    session, _hub = make_session()
    session.load(make_manifest(('left', 30, b'L')))   # nothing for 'right'

    assert session.member('sim-a').target.intent is Intent.READY
    assert session.member('sim-b').target.intent is Intent.DETACHED


def test_load_mirrors_shared_strip_id():
    # Two devices configured with the same strip_id both load the one strip.
    session, _hub = make_session(SHARED_STRIP)
    session.load(make_manifest(('wall', 30, b'W')))

    a = session.member('sim-a').target
    b = session.member('sim-b').target
    assert a.intent is b.intent is Intent.READY
    assert a.blob == b.blob == b'W'
    assert a.program_token == b.program_token == (1, 'wall')


def test_load_allows_strip_shorter_than_device():
    # A blob compiled for fewer pixels than the device has is legal; the DSL
    # allows an authored length below the configured one, so only longer fails.
    session, _hub = make_session()                       # sim-a 'left', 30 px
    session.load(make_manifest(('left', 10, b'L'), ('right', 30, b'R')))
    assert session.member('sim-a').target.intent is Intent.READY
    assert session.member('sim-a').target.strip_length == 10


def test_load_rejects_strip_longer_than_device():
    # 'left' compiled for 60 px, but sim-a is a 30 px device.
    session, _hub = make_session()
    with pytest.raises(ValueError):
        session.load(make_manifest(('left', 60, b'X')))


def test_load_rejects_unserved_manifest_strip():
    # A manifest strip whose strip_id no configured device carries is an error,
    # not a silent drop (a typo, or a device missing from the config).
    session, _hub = make_session()                       # devices: left, right
    with pytest.raises(ValueError):
        session.load(make_manifest(('left', 30, b'L'), ('ghost', 30, b'G')))


def test_rejected_load_leaves_session_untouched():
    session, _hub = make_session()
    with pytest.raises(ValueError):
        session.load(make_manifest(('left', 30, b'L'), ('ghost', 30, b'G')))
    # Routing was rejected before any commit: nothing moved.
    assert session.session_id == 0
    assert session.state is SessionState.IDLE
    assert session.member('sim-a').target.intent is Intent.DETACHED


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


# ---------------------------------------------------------------------------
# Reconciler (B1): profile -> load -> park / start
# ---------------------------------------------------------------------------

from elemctl.session import _UNSYNCED_BACKOFF_TICKS


def test_fresh_attach_ladder_profile_then_load_then_park():
    session, hub = make_session(SINGLE)
    session.load(make_manifest(('main', 30, b'M')))
    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False))

    tick(session)
    op, on_ack = hub.last_send('sim-a')
    assert op == wire.CMD_SET_PROFILE
    on_ack(ack_ok())

    tick(session)
    op, on_ack = hub.last_send('sim-a')
    assert op == wire.CMD_LOAD
    on_ack(ack_ok())

    m = session.member('sim-a')
    assert m.profile_state is ProfileState.ASSUMED_MATCH
    assert m.phase is DeviceState.LOADED
    assert m.serving is True

    # READY parks at LOADED: no further command.
    before = len(hub.sent)
    tick(session)
    assert len(hub.sent) == before


def test_one_command_in_flight_per_member():
    session, hub = make_session(SINGLE)
    session.load(make_manifest(('main', 30, b'M')))
    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False))
    tick(session)                       # sends SET_PROFILE, now in flight
    n = len(hub.sent)
    tick(session)                       # no ACK yet -> nothing new
    assert len(hub.sent) == n


def test_play_starts_from_loaded():
    session, hub = make_session(SINGLE)
    drive_to_loaded(session, hub)

    session.play()
    assert session.state is SessionState.PLAYING

    tick(session)
    op, on_ack = hub.last_send('sim-a')
    assert op == wire.CMD_START
    on_ack(ack_ok())
    assert session.member('sim-a').phase is DeviceState.PLAYING


def test_stop_parks_back_to_loaded():
    session, hub = make_session(SINGLE)
    drive_to_loaded(session, hub)
    session.play()
    tick(session)
    _, on_ack = hub.last_send('sim-a')
    on_ack(ack_ok())                    # now PLAYING

    session.stop()
    tick(session)
    op, on_ack = hub.last_send('sim-a')
    assert op == wire.CMD_STOP
    on_ack(ack_ok())
    assert session.member('sim-a').phase is DeviceState.LOADED


def test_mirrored_strip_drives_both_devices():
    session, hub = make_session(SHARED_STRIP)
    session.load(make_manifest(('wall', 30, b'W')))
    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False),
             DeviceConnected(uid='sim-b', boot_token=1, rebooted=False))
    tick(session)
    ops = {uid: encoded[4] for uid, encoded, _ in hub.sent}
    assert ops == {'sim-a': wire.CMD_SET_PROFILE, 'sim-b': wire.CMD_SET_PROFILE}


def test_inflight_ack_applies_after_retarget():
    # An ACK reports what the device actually did, so it applies even if the
    # target changed while it was in flight; the reconciler then converges.
    session, hub = make_session(SINGLE)
    session.load(make_manifest(('main', 30, b'M')))
    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False))
    tick(session)
    _, on_ack = hub.last_send('sim-a')   # SET_PROFILE in flight

    session.load(make_manifest(('main', 30, b'M2')))   # retarget while in flight
    on_ack(ack_ok())                     # the device really set its profile
    assert session.member('sim-a').profile_state is ProfileState.ASSUMED_MATCH

    tick(session)                        # reconciler now loads the new program
    op, _ = hub.last_send('sim-a')
    assert op == wire.CMD_LOAD


def test_start_in_flight_then_stop_converges_to_loaded():
    # Start sent, then stop() before the ACK: applying the Start ACK records
    # PLAYING, and the reconciler issues the corrective Stop.
    session, hub = make_session(SINGLE)
    drive_to_loaded(session, hub)
    session.play()
    tick(session)
    op, start_ack = hub.last_send('sim-a')
    assert op == wire.CMD_START

    session.stop()                       # retarget to READY while Start in flight
    start_ack(ack_ok())                  # device really entered PLAYING
    assert session.member('sim-a').phase is DeviceState.PLAYING

    tick(session)
    op, stop_ack = hub.last_send('sim-a')
    assert op == wire.CMD_STOP
    stop_ack(ack_ok())
    assert session.member('sim-a').phase is DeviceState.LOADED


def test_stop_in_flight_then_play_converges_to_playing():
    # Stop sent, then play() before the ACK: the device returns to LOADED and
    # the reconciler then starts it (authorization keys off the loaded program,
    # not the transient phase).
    session, hub = make_session(SINGLE)
    drive_to_loaded(session, hub)
    session.play()
    tick(session)
    _, on_ack = hub.last_send('sim-a')
    on_ack(ack_ok())                     # PLAYING

    session.stop()
    tick(session)
    op, stop_ack = hub.last_send('sim-a')
    assert op == wire.CMD_STOP

    session.play()                       # restart while Stop in flight
    stop_ack(ack_ok())                   # device really stopped -> LOADED
    assert session.member('sim-a').phase is DeviceState.LOADED

    tick(session)
    op, start_ack = hub.last_send('sim-a')
    assert op == wire.CMD_START
    start_ack(ack_ok())
    assert session.member('sim-a').phase is DeviceState.PLAYING


def test_unsynced_backs_off_then_retries():
    session, hub = make_session(SINGLE)
    drive_to_loaded(session, hub)
    session.play()
    tick(session)
    op, on_ack = hub.last_send('sim-a')
    assert op == wire.CMD_START
    on_ack(ack(wire.ACK_UNSYNCED))

    m = session.member('sim-a')
    assert m.last_refusal == ('start', wire.ACK_UNSYNCED)
    assert m.phase is DeviceState.LOADED       # not advanced

    n = len(hub.sent)
    tick(session)                              # inside backoff window
    assert len(hub.sent) == n

    for _ in range(_UNSYNCED_BACKOFF_TICKS + 1):
        tick(session)
    op, _ = hub.last_send('sim-a')
    assert op == wire.CMD_START                # retried after backoff


def test_terminal_failure_blocks_and_emits():
    session, hub = make_session(SINGLE)
    session.load(make_manifest(('main', 30, b'M')))
    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False))
    tick(session)
    _, on_ack = hub.last_send('sim-a')
    on_ack(ack_ok())                    # profile ok
    tick(session)
    _, on_ack = hub.last_send('sim-a')  # LOAD
    on_ack(ack(wire.ACK_ERROR))         # terminal failure

    m = session.member('sim-a')
    assert m.blocked == ('load', wire.ACK_ERROR)

    n = len(hub.sent)
    events = tick(session)
    assert MemberCommandFailed(uid='sim-a', command='load',
                               status=wire.ACK_ERROR) in events
    assert len(hub.sent) == n           # blocked: no further commands


def test_profile_mismatch_reprofiles():
    session, hub = make_session(SINGLE)
    session.load(make_manifest(('main', 30, b'M')))
    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False))
    tick(session)
    _, on_ack = hub.last_send('sim-a')
    on_ack(ack_ok())                    # profile ASSUMED
    tick(session)
    _, on_ack = hub.last_send('sim-a')  # LOAD
    on_ack(ack(wire.ACK_PROFILE_MISMATCH))

    m = session.member('sim-a')
    assert m.profile_state is ProfileState.UNKNOWN
    assert m._loaded_token is None

    tick(session)
    op, _ = hub.last_send('sim-a')
    assert op == wire.CMD_SET_PROFILE


def test_load_wrong_state_reprofiles():
    session, hub = make_session(SINGLE)
    session.load(make_manifest(('main', 30, b'M')))
    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False))
    tick(session)
    _, on_ack = hub.last_send('sim-a')
    on_ack(ack_ok())
    tick(session)
    _, on_ack = hub.last_send('sim-a')  # LOAD
    on_ack(ack(wire.ACK_WRONG_STATE))   # no hardware profile on the device

    assert session.member('sim-a').profile_state is ProfileState.UNKNOWN
    tick(session)
    op, _ = hub.last_send('sim-a')
    assert op == wire.CMD_SET_PROFILE


def test_start_wrong_state_reloads_keeping_profile():
    session, hub = make_session(SINGLE)
    drive_to_loaded(session, hub)
    session.play()
    tick(session)
    _, on_ack = hub.last_send('sim-a')  # START
    on_ack(ack(wire.ACK_WRONG_STATE))   # phase drift

    m = session.member('sim-a')
    assert m.profile_state is ProfileState.ASSUMED_MATCH   # profile kept
    assert m._loaded_token is None and m.phase is DeviceState.UNKNOWN

    tick(session)
    op, _ = hub.last_send('sim-a')
    assert op == wire.CMD_LOAD


def test_detached_member_with_stale_program_is_stopped_once():
    session, hub = make_session(TWO_STRIPS)
    session.load(make_manifest(('main', 30, b'M'), ('side', 30, b'S')))
    hub.emit(DeviceConnected(uid='sim-b', boot_token=1, rebooted=False))
    tick(session)
    _, on_ack = hub.last_send('sim-b')
    on_ack(ack_ok())                    # profile
    tick(session)
    _, on_ack = hub.last_send('sim-b')
    on_ack(ack_ok())                    # loaded 'side'; sim-b serving
    assert session.member('sim-b').serving is True

    # New manifest drops 'side': sim-b detaches but still serves stale content.
    session.load(make_manifest(('main', 30, b'M')))
    assert session.member('sim-b').target.intent is Intent.DETACHED

    tick(session)
    op, on_ack = hub.last_send('sim-b')
    assert op == wire.CMD_STOP
    on_ack(ack_ok())
    assert session.member('sim-b').serving is False

    n = len(hub.sent)
    tick(session)
    assert len(hub.sent) == n           # stopped once, then idle


def test_late_joiner_during_play_is_not_started():
    session, hub = make_session(TWO_STRIPS)
    session.load(make_manifest(('main', 30, b'M'), ('side', 30, b'S')))

    # Bring only sim-a to LOADED, then play: sim-a is the authorized cohort.
    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False))
    tick(session)
    _, on_ack = hub.last_send('sim-a'); on_ack(ack_ok())
    tick(session)
    _, on_ack = hub.last_send('sim-a'); on_ack(ack_ok())   # sim-a LOADED

    session.play()
    tick(session)
    op, on_ack = hub.last_send('sim-a')
    assert op == wire.CMD_START
    on_ack(ack_ok())                                       # sim-a playing

    # sim-b attaches late, loads, but must never be Started in B1.
    hub.emit(DeviceConnected(uid='sim-b', boot_token=1, rebooted=False))
    tick(session)
    op, on_ack = hub.last_send('sim-b')
    assert op == wire.CMD_SET_PROFILE
    on_ack(ack_ok())
    tick(session)
    _, on_ack = hub.last_send('sim-b'); on_ack(ack_ok())   # sim-b LOADED

    for _ in range(3):
        tick(session)
    b_ops = [encoded[4] for uid, encoded, _ in hub.sent if uid == 'sim-b']
    assert wire.CMD_START not in b_ops
    assert session.member('sim-b').phase is DeviceState.LOADED   # parked


def test_play_while_playing_is_refused():
    session, hub = make_session(SINGLE)
    drive_to_loaded(session, hub)
    session.play()
    tick(session)
    _, on_ack = hub.last_send('sim-a'); on_ack(ack_ok())   # actually PLAYING
    with pytest.raises(ValueError, match='only valid from a loaded'):
        session.play()


def test_stale_load_failure_after_new_load_does_not_block():
    # An old Load failing after the operator loaded a new program must not
    # block the member: the failure was about the superseded blob.
    session, hub = make_session(SINGLE)
    session.load(make_manifest(('main', 30, b'M')))
    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False))
    tick(session)
    _, on_ack = hub.last_send('sim-a'); on_ack(ack_ok())   # profile
    tick(session)
    _, load_ack = hub.last_send('sim-a')                   # Load(M) in flight

    session.load(make_manifest(('main', 30, b'M2')))       # retarget to a new blob
    load_ack(ack(wire.ACK_ERROR))                          # stale failure for M

    assert session.member('sim-a').blocked is None
    tick(session)
    op, _ = hub.last_send('sim-a')
    assert op == wire.CMD_LOAD                             # loads M2


def test_load_failure_blocks_even_after_intent_only_retarget():
    # play() while a Load is in flight is an intent-only retarget (same blob).
    # The Load's terminal failure is still about the program we want, so it
    # must block immediately, not be discarded as stale.
    session, hub = make_session(SINGLE)
    session.load(make_manifest(('main', 30, b'M')))
    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False))
    tick(session)
    _, on_ack = hub.last_send('sim-a'); on_ack(ack_ok())   # profile
    tick(session)
    _, load_ack = hub.last_send('sim-a')                   # Load(M) in flight

    session.play()                                         # READY -> PLAYING, same program
    load_ack(ack(wire.ACK_ERROR))
    assert session.member('sim-a').blocked == ('load', wire.ACK_ERROR)


def test_stale_start_refusal_after_stop_is_ignored():
    # A Start refusal arriving after the operator stopped is for an abandoned
    # intent: it must not record a refusal or set a retry backoff.
    session, hub = make_session(SINGLE)
    drive_to_loaded(session, hub)
    session.play()
    tick(session)
    op, start_ack = hub.last_send('sim-a')
    assert op == wire.CMD_START
    session.stop()                                         # PLAYING -> READY while Start in flight
    start_ack(ack(wire.ACK_UNSYNCED))

    m = session.member('sim-a')
    assert m.last_refusal is None and m.retry_at_tick == 0


def test_play_refused_while_start_in_flight():
    session, hub = make_session(SINGLE)
    drive_to_loaded(session, hub)
    session.play()
    tick(session)
    op, _ = hub.last_send('sim-a')
    assert op == wire.CMD_START                            # in flight, unacked
    session.stop()
    with pytest.raises(ValueError, match='stopped first'):
        session.play()


def test_play_refused_after_stop_before_tick():
    # stop() flips session state to LOADED, but the device is still PLAYING
    # until the Stop is sent and ACKed; play() must not restamp the anchor yet.
    session, hub = make_session(SINGLE)
    drive_to_loaded(session, hub)
    session.play()
    tick(session)
    _, on_ack = hub.last_send('sim-a'); on_ack(ack_ok())   # PLAYING
    session.stop()                                         # no tick: no Stop sent
    with pytest.raises(ValueError, match='stopped first'):
        session.play()


def test_terminal_block_survives_play_stop_but_clears_on_load():
    session, hub = make_session(SINGLE)
    session.load(make_manifest(('main', 30, b'M')))
    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False))
    tick(session)
    _, on_ack = hub.last_send('sim-a'); on_ack(ack_ok())   # profile
    tick(session)
    _, on_ack = hub.last_send('sim-a'); on_ack(ack(wire.ACK_ERROR))   # terminal LOAD
    assert session.member('sim-a').blocked == ('load', wire.ACK_ERROR)

    # play() retargets the same program: it must NOT forgive the block.
    session.play()
    n = len(hub.sent)
    tick(session)
    assert len(hub.sent) == n
    assert session.member('sim-a').blocked == ('load', wire.ACK_ERROR)

    # A fresh load is a new program: the block clears.
    session.load(make_manifest(('main', 30, b'M2')))
    assert session.member('sim-a').blocked is None


def test_pause_sends_pause_and_resume_sends_resume():
    session, hub = make_session(SINGLE)
    drive_to_playing(session, hub)

    session.pause()
    assert session.state is SessionState.PAUSED
    tick(session)
    op, on_ack = hub.last_send('sim-a')
    assert op == wire.CMD_PAUSE
    on_ack(ack_ok())
    assert session.member('sim-a').phase is DeviceState.PAUSED

    session.resume()
    assert session.state is SessionState.PLAYING
    tick(session)
    op, on_ack = hub.last_send('sim-a')
    assert op == wire.CMD_RESUME
    on_ack(ack_ok())
    assert session.member('sim-a').phase is DeviceState.PLAYING


def test_pause_requires_playing():
    session, hub = make_session(SINGLE)
    drive_to_loaded(session, hub)            # LOADED, not playing
    with pytest.raises(ValueError, match='playing'):
        session.pause()


def test_resume_requires_paused():
    session, hub = make_session(SINGLE)
    drive_to_playing(session, hub)
    with pytest.raises(ValueError, match='paused'):
        session.resume()


def test_pause_captures_cursor_and_resume_reanchors():
    session, hub = make_session(SINGLE)
    drive_to_playing(session, hub)
    session._clock_us.now_us += 2_000_000   # 2s of playback
    session.pause()
    assert session.cursor_us == 2_000_000
    tick(session)
    _, on_ack = hub.last_send('sim-a'); on_ack(ack_ok())   # settle: PAUSED

    session._clock_us.now_us += 5_000_000   # idle while paused
    session.resume()
    # program time continues from the cursor: now - anchor == cursor.
    assert session._clock_us() - session.program_start_us == session.cursor_us


def test_resume_refused_before_pause_settles():
    session, hub = make_session(SINGLE)
    drive_to_playing(session, hub)
    session.pause()                          # no tick: no Pause sent, still PLAYING
    with pytest.raises(ValueError, match='settle'):
        session.resume()


def test_resume_refused_while_pause_in_flight():
    session, hub = make_session(SINGLE)
    drive_to_playing(session, hub)
    session.pause()
    tick(session)
    op, on_ack = hub.last_send('sim-a')
    assert op == wire.CMD_PAUSE               # Pause in flight, unacked
    with pytest.raises(ValueError, match='settle'):
        session.resume()
    on_ack(ack_ok())                          # now actually PAUSED
    session.resume()
    tick(session)
    op, _ = hub.last_send('sim-a')
    assert op == wire.CMD_RESUME


def test_play_from_paused_is_refused():
    session, hub = make_session(SINGLE)
    drive_to_playing(session, hub)
    session.pause()
    with pytest.raises(ValueError, match='only valid from a loaded'):
        session.play()


def test_pause_refused_before_play_starts():
    # play(); pause() before the Start is even sent would clear the cohort's
    # start authorization and strand the device at LOADED.
    session, hub = make_session(SINGLE)
    drive_to_loaded(session, hub)
    session.play()                            # cohort authorized, Start not sent
    with pytest.raises(ValueError, match='settle'):
        session.pause()


def test_pause_refused_while_start_in_flight():
    session, hub = make_session(SINGLE)
    drive_to_loaded(session, hub)
    session.play()
    tick(session)
    op, on_ack = hub.last_send('sim-a')
    assert op == wire.CMD_START               # in flight, unacked
    with pytest.raises(ValueError, match='settle'):
        session.pause()
    on_ack(ack_ok())                          # now actually PLAYING
    session.pause()                           # ok
    tick(session)
    op, _ = hub.last_send('sim-a')
    assert op == wire.CMD_PAUSE


def test_pause_refused_before_resume_settles():
    session, hub = make_session(SINGLE)
    drive_to_playing(session, hub)
    session.pause()
    tick(session)
    _, on_ack = hub.last_send('sim-a'); on_ack(ack_ok())   # settle: PAUSED
    session.resume()                          # state PLAYING, Resume not sent yet
    with pytest.raises(ValueError, match='settle'):
        session.pause()


def test_pause_refused_while_resume_in_flight():
    session, hub = make_session(SINGLE)
    drive_to_playing(session, hub)
    session.pause()
    tick(session)
    _, on_ack = hub.last_send('sim-a'); on_ack(ack_ok())   # PAUSED
    session.resume()
    tick(session)
    op, on_ack = hub.last_send('sim-a')
    assert op == wire.CMD_RESUME              # Resume in flight, unacked
    with pytest.raises(ValueError, match='settle'):
        session.pause()
    on_ack(ack_ok())                          # now actually PLAYING
    session.pause()
    tick(session)
    op, _ = hub.last_send('sim-a')
    assert op == wire.CMD_PAUSE


def test_play_refused_from_paused_even_when_member_detached():
    # A detached member used to let play() slip the unsettled guard from a
    # PAUSED session and wipe the paused cursor; play() is now refused on state.
    session, hub = make_session(SINGLE)
    drive_to_playing(session, hub)
    session._clock_us.now_us += 1_000_000
    session.pause()
    tick(session)
    _, on_ack = hub.last_send('sim-a'); on_ack(ack_ok())   # PAUSED
    cursor = session.cursor_us

    hub.emit(DeviceDisconnected(uid='sim-a', reason='dropped'))
    tick(session)
    with pytest.raises(ValueError, match='only valid from a loaded'):
        session.play()
    assert session.cursor_us == cursor                     # paused cursor preserved


def test_unsynced_start_does_not_block_pause():
    # A device that keeps refusing Start (Unsynced) must not block pausing the
    # rest of a running show; a surfaced refusal releases the pause guard.
    session, hub = make_session(SINGLE)
    drive_to_loaded(session, hub)
    session.play()
    tick(session)
    op, on_ack = hub.last_send('sim-a')
    assert op == wire.CMD_START
    on_ack(ack(wire.ACK_UNSYNCED))      # start refused, retry pending, start_authorized still set
    session.pause()                     # must not raise
    assert session.state is SessionState.PAUSED


def test_pause_refused_while_resume_unsynced():
    # A refused Resume leaves the device physically PAUSED while the session is
    # PLAYING; pause() must not capture a cursor from the re-anchored clock.
    session, hub = make_session(SINGLE)
    drive_to_playing(session, hub)
    session.pause()
    tick(session)
    _, on_ack = hub.last_send('sim-a'); on_ack(ack_ok())   # PAUSED
    session.resume()
    tick(session)
    op, on_ack = hub.last_send('sim-a')
    assert op == wire.CMD_RESUME
    on_ack(ack(wire.ACK_UNSYNCED))      # resume refused; device still PAUSED
    with pytest.raises(ValueError, match='settle'):
        session.pause()


def test_pause_refused_while_start_retry_in_flight():
    # After an Unsynced Start, a retry Start in flight must still block pause;
    # otherwise a pause cursor is captured just before the retry could ACK Ok.
    session, hub = make_session(SINGLE)
    drive_to_loaded(session, hub)
    session.play()
    tick(session)
    op, on_ack = hub.last_send('sim-a')
    assert op == wire.CMD_START
    on_ack(ack(wire.ACK_UNSYNCED))      # refused, backoff
    for _ in range(_UNSYNCED_BACKOFF_TICKS + 1):
        tick(session)
    op, _ = hub.last_send('sim-a')
    assert op == wire.CMD_START          # retry now in flight
    with pytest.raises(ValueError, match='settle'):
        session.pause()


def test_reboot_between_set_profile_and_load():
    session, hub = make_session(SINGLE)
    session.load(make_manifest(('main', 30, b'M')))
    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False))
    tick(session)
    op, _ = hub.last_send('sim-a')
    assert op == wire.CMD_SET_PROFILE

    # The reboot drops the link (its pending ACK dies with it) and reconnects
    # with a new boot_token; the ladder restarts fresh.
    hub.emit(DeviceDisconnected(uid='sim-a', reason='reboot'),
             DeviceConnected(uid='sim-a', boot_token=2, rebooted=True))
    tick(session)
    op, fresh_ack = hub.last_send('sim-a')
    assert op == wire.CMD_SET_PROFILE

    fresh_ack(ack_ok())                 # post-reboot ACK advances
    tick(session)
    op, _ = hub.last_send('sim-a')
    assert op == wire.CMD_LOAD


# ---------------------------------------------------------------------------
# End of program (B2-end): the controller ends the session by the clock the
# devices follow, since there is no end event or query.
# ---------------------------------------------------------------------------

def test_program_ends_by_clock():
    session, hub = make_session(SINGLE)
    drive_to_playing(session, hub)
    session._clock_us.now_us += 10_000_000   # past the 10s duration
    tick(session)
    assert session.state is SessionState.ENDED
    assert session.member('sim-a').phase is DeviceState.ENDED


def test_ended_member_parks_with_no_replay():
    session, hub = make_session(SINGLE)
    drive_to_playing(session, hub)
    session._clock_us.now_us += 10_000_000
    tick(session)
    assert session.state is SessionState.ENDED

    n = len(hub.sent)
    for _ in range(3):
        tick(session)
    assert len(hub.sent) == n                # no auto-replay
    assert session.member('sim-a').phase is DeviceState.ENDED


def test_pause_refused_from_ended():
    session, hub = make_session(SINGLE)
    drive_to_playing(session, hub)
    session._clock_us.now_us += 10_000_000
    tick(session)
    assert session.state is SessionState.ENDED
    # The bug this round fixes: from ENDED the device ignores Pause but ACKs Ok,
    # which used to record a false PAUSED. The state guard refuses it outright.
    with pytest.raises(ValueError, match='playing'):
        session.pause()


def test_resume_refused_from_ended():
    session, hub = make_session(SINGLE)
    drive_to_playing(session, hub)
    session._clock_us.now_us += 10_000_000
    tick(session)
    with pytest.raises(ValueError, match='paused'):
        session.resume()


def test_play_replays_from_ended_without_reload():
    session, hub = make_session(SINGLE)
    drive_to_playing(session, hub)
    session._clock_us.now_us += 10_000_000
    tick(session)
    assert session.state is SessionState.ENDED
    assert session.member('sim-a').phase is DeviceState.ENDED

    session.play()
    assert session.state is SessionState.PLAYING
    tick(session)
    op, on_ack = hub.last_send('sim-a')
    assert op == wire.CMD_START              # Start from ENDED, no reload
    on_ack(ack_ok())
    assert session.member('sim-a').phase is DeviceState.PLAYING
    assert session.program_start_us == session._clock_us()   # anchor re-stamped


def test_late_joiner_not_marked_ended():
    session, hub = make_session(TWO_STRIPS)
    session.load(make_manifest(('main', 30, b'M'), ('side', 30, b'S')))

    # sim-a plays; sim-b attaches late, loads, but B1 parks it at LOADED.
    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False))
    tick(session)
    _, on_ack = hub.last_send('sim-a'); on_ack(ack_ok())
    tick(session)
    _, on_ack = hub.last_send('sim-a'); on_ack(ack_ok())   # sim-a LOADED

    session.play()
    tick(session)
    _, on_ack = hub.last_send('sim-a'); on_ack(ack_ok())   # sim-a PLAYING

    hub.emit(DeviceConnected(uid='sim-b', boot_token=1, rebooted=False))
    tick(session)
    _, on_ack = hub.last_send('sim-b'); on_ack(ack_ok())   # profile
    tick(session)
    _, on_ack = hub.last_send('sim-b'); on_ack(ack_ok())   # sim-b LOADED, parked

    session._clock_us.now_us += 10_000_000
    tick(session)
    assert session.state is SessionState.ENDED
    assert session.member('sim-a').phase is DeviceState.ENDED    # was playing
    assert session.member('sim-b').phase is DeviceState.LOADED   # never started


def test_stop_from_ended_parks_ready_without_stop():
    session, hub = make_session(SINGLE)
    drive_to_playing(session, hub)
    session._clock_us.now_us += 10_000_000
    tick(session)
    assert session.state is SessionState.ENDED

    session.stop()
    assert session.state is SessionState.LOADED
    assert session.member('sim-a').target.intent is Intent.READY

    # ENDED already shows black, so READY needs no Stop; the phase honestly
    # stays ENDED (we sent nothing).
    n = len(hub.sent)
    tick(session)
    assert len(hub.sent) == n
    assert session.member('sim-a').phase is DeviceState.ENDED


def test_end_not_detected_while_paused():
    # Paused, the anchor is stale and now - program_start_us grows past the
    # duration with playback frozen; end detection must not fire.
    session, hub = make_session(SINGLE)
    drive_to_playing(session, hub)
    session._clock_us.now_us += 1_000_000
    session.pause()
    tick(session)
    _, on_ack = hub.last_send('sim-a'); on_ack(ack_ok())   # PAUSED

    session._clock_us.now_us += 20_000_000   # far past the duration, but paused
    tick(session)
    assert session.state is SessionState.PAUSED
    assert session.member('sim-a').phase is DeviceState.PAUSED


def test_start_acked_after_end_records_ended():
    # A Start still in flight when the clock crosses duration: the session ends
    # with the member still at LOADED, and the late Ok records ENDED (the device
    # started at an anchor past duration and ends on its next render).
    session, hub = make_session(SINGLE)
    drive_to_loaded(session, hub)
    session.play()
    tick(session)
    op, start_ack = hub.last_send('sim-a')
    assert op == wire.CMD_START              # in flight, unacked

    session._clock_us.now_us += 10_000_000
    tick(session)
    assert session.state is SessionState.ENDED
    assert session.member('sim-a').phase is DeviceState.LOADED   # Start unacked

    start_ack(ack_ok())
    assert session.member('sim-a').phase is DeviceState.ENDED
    assert session.member('sim-a').start_authorized is False


def test_resume_acked_after_end_records_ended():
    session, hub = make_session(SINGLE)
    drive_to_playing(session, hub)
    session._clock_us.now_us += 1_000_000
    session.pause()
    tick(session)
    _, on_ack = hub.last_send('sim-a'); on_ack(ack_ok())   # PAUSED at 1s cursor

    session.resume()
    tick(session)
    op, resume_ack = hub.last_send('sim-a')
    assert op == wire.CMD_RESUME             # in flight, unacked

    session._clock_us.now_us += 10_000_000
    tick(session)
    assert session.state is SessionState.ENDED
    assert session.member('sim-a').phase is DeviceState.PAUSED   # Resume unacked

    resume_ack(ack_ok())
    assert session.member('sim-a').phase is DeviceState.ENDED


def test_unsynced_start_then_end_does_not_retry_start():
    # A Start refused Unsynced keeps start_authorized set; once the session
    # ends, the PLAYING-rung guard must stop the backoff retry from firing.
    session, hub = make_session(SINGLE)
    drive_to_loaded(session, hub)
    session.play()
    tick(session)
    op, on_ack = hub.last_send('sim-a')
    assert op == wire.CMD_START
    on_ack(ack(wire.ACK_UNSYNCED))           # refused; authorized + backoff

    session._clock_us.now_us += 10_000_000
    tick(session)
    assert session.state is SessionState.ENDED

    n_start = sum(1 for _u, e, _a in hub.sent if e[4] == wire.CMD_START)
    for _ in range(_UNSYNCED_BACKOFF_TICKS + 2):
        tick(session)
    after = sum(1 for _u, e, _a in hub.sent if e[4] == wire.CMD_START)
    assert after == n_start                  # no retry into an ended session


def test_unsynced_resume_then_end_does_not_retry_resume():
    session, hub = make_session(SINGLE)
    drive_to_playing(session, hub)
    session._clock_us.now_us += 1_000_000
    session.pause()
    tick(session)
    _, on_ack = hub.last_send('sim-a'); on_ack(ack_ok())   # PAUSED
    session.resume()
    tick(session)
    op, on_ack = hub.last_send('sim-a')
    assert op == wire.CMD_RESUME
    on_ack(ack(wire.ACK_UNSYNCED))           # refused; device stays PAUSED

    session._clock_us.now_us += 10_000_000
    tick(session)
    assert session.state is SessionState.ENDED
    assert session.member('sim-a').phase is DeviceState.PAUSED

    n_resume = sum(1 for _u, e, _a in hub.sent if e[4] == wire.CMD_RESUME)
    for _ in range(_UNSYNCED_BACKOFF_TICKS + 2):
        tick(session)
    after = sum(1 for _u, e, _a in hub.sent if e[4] == wire.CMD_RESUME)
    assert after == n_resume


def test_replay_from_ended_requires_stopping_a_paused_leftover():
    # A device left PAUSED by a refused Resume keeps replay from firing
    # directly: play() would otherwise resume it from its stale cursor instead
    # of restarting at 0, so it is refused until stopped. stop() normalizes it
    # to LOADED, then play() replays from 0 (Start, not Resume). The full
    # drive-through-Stop on replay belongs with B2b's rejoin normalization.
    session, hub = make_session(SINGLE)
    drive_to_playing(session, hub)
    session._clock_us.now_us += 1_000_000
    session.pause()
    tick(session)
    _, on_ack = hub.last_send('sim-a'); on_ack(ack_ok())   # PAUSED
    session.resume()
    tick(session)
    op, on_ack = hub.last_send('sim-a')
    assert op == wire.CMD_RESUME
    on_ack(ack(wire.ACK_UNSYNCED))           # refused; device stays PAUSED

    session._clock_us.now_us += 10_000_000
    tick(session)
    assert session.state is SessionState.ENDED
    assert session.member('sim-a').phase is DeviceState.PAUSED

    with pytest.raises(ValueError, match='stopped first'):
        session.play()

    session.stop()
    tick(session)
    op, on_ack = hub.last_send('sim-a')
    assert op == wire.CMD_STOP
    on_ack(ack_ok())                         # PAUSED -> LOADED
    session.play()
    tick(session)
    op, _ = hub.last_send('sim-a')
    assert op == wire.CMD_START              # replays from 0, not Resume


def _frame(uid='sim-a', t_program=0.02, pixels=30):
    return FramePreview(uid=uid, frame_index=1, t_program=t_program,
                        rgb=b'\x00' * (pixels * 3))


def test_preview_frame_assembled_while_playing():
    session, hub = make_session(SINGLE)
    drive_to_playing(session, hub)
    session.set_preview_enabled(True)
    session._clock_us.now_us += 1_000_000      # live runs ahead of the frame
    hub.frames = [_frame()]                     # t_program 0.02, well behind live
    tick(session)
    frames = session.drain_preview_frames()
    assert len(frames) == 1
    assert frames[0].frame_index == 1            # floor(0.02 * 50)
    assert frames[0].strips == [b'\x00' * 90]


def test_preview_frames_dropped_when_disabled():
    session, hub = make_session(SINGLE)
    drive_to_playing(session, hub)               # playing, but no subscriber
    hub.frames = [_frame()]
    tick(session)
    assert session.drain_preview_frames() == []


def test_preview_frame_dropped_when_ahead_of_live():
    # live is ~0 just after play; a packet carrying a much larger program time
    # is a straggler from a previous run and must be dropped.
    session, hub = make_session(SINGLE)
    drive_to_playing(session, hub)
    session.set_preview_enabled(True)
    hub.frames = [_frame(t_program=5.0)]
    tick(session)
    assert session.drain_preview_frames() == []


def test_preview_frame_dropped_when_not_playing():
    session, hub = make_session(SINGLE)
    drive_to_loaded(session, hub)                # LOADED, not playing
    session.set_preview_enabled(True)
    hub.frames = [_frame()]
    tick(session)
    assert session.drain_preview_frames() == []


def test_preview_frame_dropped_on_wrong_length():
    session, hub = make_session(SINGLE)
    drive_to_playing(session, hub)
    session.set_preview_enabled(True)
    session._clock_us.now_us += 1_000_000
    hub.frames = [_frame(pixels=10)]             # 30 bytes, strip wants 90
    tick(session)
    assert session.drain_preview_frames() == []


def test_mixed_ended_and_paused_leftover_replays_whole_cohort():
    # An ended session with a mix of ENDED members and one PAUSED leftover (a
    # refused Resume): stop() then play() must replay every member. play()
    # authorizes by loaded token, not phase, so the normalized LOADED member is
    # not mistaken for an unauthorized late joiner.
    session, hub = make_session(SHARED_STRIP)   # sim-a, sim-b both serve 'wall'
    session.load(make_manifest(('wall', 30, b'W')))
    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False),
             DeviceConnected(uid='sim-b', boot_token=1, rebooted=False))
    tick(session)
    for uid in ('sim-a', 'sim-b'):
        _, on_ack = hub.last_send(uid); on_ack(ack_ok())   # profiles
    tick(session)
    for uid in ('sim-a', 'sim-b'):
        _, on_ack = hub.last_send(uid); on_ack(ack_ok())   # both LOADED

    session.play()
    tick(session)
    for uid in ('sim-a', 'sim-b'):
        _, on_ack = hub.last_send(uid); on_ack(ack_ok())   # both PLAYING

    session._clock_us.now_us += 1_000_000
    session.pause()
    tick(session)
    for uid in ('sim-a', 'sim-b'):
        _, on_ack = hub.last_send(uid); on_ack(ack_ok())   # both PAUSED

    session.resume()
    tick(session)
    _, a_ack = hub.last_send('sim-a'); a_ack(ack_ok())             # sim-a PLAYING
    _, b_ack = hub.last_send('sim-b'); b_ack(ack(wire.ACK_UNSYNCED))  # sim-b stays PAUSED

    session._clock_us.now_us += 10_000_000
    tick(session)
    assert session.state is SessionState.ENDED
    assert session.member('sim-a').phase is DeviceState.ENDED
    assert session.member('sim-b').phase is DeviceState.PAUSED

    session.stop()
    tick(session)
    op, b_ack = hub.last_send('sim-b')
    assert op == wire.CMD_STOP                # only the paused leftover needs it
    b_ack(ack_ok())                           # sim-b PAUSED -> LOADED

    session.play()
    tick(session)
    a_op = next(e[4] for u, e, _ in reversed(hub.sent) if u == 'sim-a')
    b_op = next(e[4] for u, e, _ in reversed(hub.sent) if u == 'sim-b')
    assert a_op == wire.CMD_START             # ENDED member replays from 0
    assert b_op == wire.CMD_START             # normalized LOADED member too
