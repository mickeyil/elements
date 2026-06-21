"""ControllerService tests: command dispatch, publishing, and the load path.

Socket-free: a FakeHub stands in for the device hub and the service is driven
through handle_cmd()/tick_once()/snapshot_messages() directly. The load path
compiles a real (tiny) program against the configured topology.
"""

import json

from elemctl.config import Config, DeviceConfig, load_config_obj
from elemctl.controller_protocol import KIND_FRAME, KIND_JSON, parse_json_payload
from elemctl.hub import DeviceConnected, HubPoll
from elemctl.library import ProgramLibrary
from elemctl.service import ControllerService
from elemctl import wire
from elemctl.wire import AckMsg, FramePreview

import pytest


PROGRAM = (
    "BEAT = 1.0\n"
    "DURATION = 4.0\n"
    "from elements.dsl import sec, strip, paint\n"
    "main = strip('main')\n"
    "p = paint(color='white')\n"
    "p.schedule(main.pixels('0'), at=0, duration=sec(DURATION))\n"
)


class FakeClock:
    def __init__(self):
        self.now_us = 1_000_000

    def __call__(self):
        return self.now_us


class FakeHub:
    def __init__(self):
        self.pending_events = []
        self.frames = []
        self.sent = []
        self.discovered = set()
        self.connected = set()
        self.disconnected = []
        self.closed = False

    def is_connected(self, uid):
        return uid in self.connected

    def emit(self, *events):
        self.pending_events.extend(events)

    def discovered_uids(self):
        return set(self.discovered)

    def disconnect(self, uid, reason='removed'):
        self.disconnected.append((uid, reason))

    def poll(self, wanted_uids):
        events, self.pending_events = self.pending_events, []
        frames, self.frames = self.frames, []
        return HubPoll(events=events, frames=frames)

    def send(self, uid, encoded, on_ack=None):
        self.sent.append((uid, encoded, on_ack))
        return True

    def last_send(self, uid=None):
        for u, encoded, on_ack in reversed(self.sent):
            if uid is None or u == uid:
                return encoded[4], on_ack
        return None, None

    def close(self):
        self.closed = True


def make_service(tmp_path, with_program=True):
    if with_program:
        (tmp_path / 'prog.py').write_text(PROGRAM)
    config = Config(discovery_port=6040, link_port=6041, frame_port=6042,
                    sync_port=6043,
                    devices=[DeviceConfig('sim-a', 'main', 30)],
                    animations_dir=str(tmp_path))
    hub = FakeHub()
    service = ControllerService(config, hub=hub, clock_us=FakeClock(),
                                library=ProgramLibrary(str(tmp_path)))
    return service, hub


def make_service_with_config(tmp_path):
    """A service backed by a real config file, so device-edit commands can
    persist (make_service leaves config_path None)."""
    (tmp_path / 'prog.py').write_text(PROGRAM)
    doc = {
        'controller': {'discovery_port': 6040, 'link_port': 6041,
                       'frame_port': 6042, 'sync_port': 6043},
        'devices': [{'device_uid': 'sim-a', 'strip_id': 'main', 'length': 30}],
    }
    config_path = tmp_path / 'config.json'
    config_path.write_text(json.dumps(doc))
    hub = FakeHub()
    service = ControllerService(load_config_obj(doc), config_path=str(config_path),
                               hub=hub, clock_us=FakeClock(),
                               library=ProgramLibrary(str(tmp_path)))
    return service, hub, config_path


def cmd(service, name, _id=1, **kw):
    return service.handle_cmd({'id': _id, 'cmd': name, **kw})


def _ack_ok(hub, uid='sim-a'):
    _, on_ack = hub.last_send(uid)
    on_ack(AckMsg(status=0, payload=b''))   # 0 == wire.ACK_OK


def drive_service_to_playing(service, hub, uid='sim-a'):
    """Load 'prog', attach the device, and ACK profile/load/start so the
    member reaches a confirmed PLAYING phase (the preview-eligible state)."""
    cmd(service, 'load', program_id='prog')
    hub.emit(DeviceConnected(uid=uid, boot_token=1, rebooted=False))
    service.tick_once(); _ack_ok(hub, uid)   # SET_PROFILE
    service.tick_once(); _ack_ok(hub, uid)   # LOAD
    cmd(service, 'play')
    service.tick_once(); _ack_ok(hub, uid)   # START


def _json(msgs):
    """Decode encoded controller-protocol JSON messages to dicts."""
    out = []
    for m in msgs:
        if m[4] == KIND_JSON:
            out.append(parse_json_payload(m[5:]))
    return out


def _by_type(dicts, type_):
    return [d for d in dicts if d.get('type') == type_]


# ---------------------------------------------------------------------------
# Load / compile path
# ---------------------------------------------------------------------------

def test_load_compiles_and_enters_loaded(tmp_path):
    service, _hub = make_service(tmp_path)
    reply = cmd(service, 'load', program_id='prog')
    assert reply['ok'] is True
    assert reply['result']['program_id'] == 'prog'

    state = _by_type(_json(service.snapshot_messages()), 'state')[0]
    assert state['session']['state'] == 'loaded'
    assert state['session']['program_id'] == 'prog'
    assert state['session']['duration'] == 4.0
    device = state['devices'][0]
    assert device['strip_id'] == 'main'
    assert device['length'] == 30
    assert device['target_intent'] == 'ready'   # routed, parked at LOADED


def test_load_unknown_program_errors(tmp_path):
    service, _hub = make_service(tmp_path)
    reply = cmd(service, 'load', program_id='nope')
    assert reply['ok'] is False
    assert 'unknown program' in reply['error']


def test_load_without_program_id_errors(tmp_path):
    service, _hub = make_service(tmp_path)
    reply = cmd(service, 'load')
    assert reply['ok'] is False


# ---------------------------------------------------------------------------
# Transport verbs
# ---------------------------------------------------------------------------

def test_play_pause_resume_stop_round_trip(tmp_path):
    service, _hub = make_service(tmp_path)
    cmd(service, 'load', program_id='prog')

    assert cmd(service, 'play')['ok'] is True
    assert service._session.state.name == 'PLAYING'
    assert cmd(service, 'pause')['ok'] is True
    assert service._session.state.name == 'PAUSED'
    assert cmd(service, 'resume')['ok'] is True
    assert service._session.state.name == 'PLAYING'
    assert cmd(service, 'stop')['ok'] is True
    assert service._session.state.name == 'LOADED'


def test_play_from_idle_refused(tmp_path):
    service, _hub = make_service(tmp_path)
    reply = cmd(service, 'play')
    assert reply['ok'] is False
    assert 'loaded' in reply['error']


def test_pause_from_loaded_refused(tmp_path):
    service, _hub = make_service(tmp_path)
    cmd(service, 'load', program_id='prog')
    reply = cmd(service, 'pause')
    assert reply['ok'] is False
    assert 'playing' in reply['error']


def test_unknown_command_errors(tmp_path):
    service, _hub = make_service(tmp_path)
    reply = cmd(service, 'frobnicate')
    assert reply['ok'] is False
    assert 'unknown command' in reply['error']


def test_shutdown_command_sets_flag(tmp_path):
    service, _hub = make_service(tmp_path)
    assert service.should_shutdown is False
    assert cmd(service, 'shutdown')['ok'] is True
    assert service.should_shutdown is True


# ---------------------------------------------------------------------------
# Publishing: state, events, catalog
# ---------------------------------------------------------------------------

def test_snapshot_carries_state_and_catalog(tmp_path):
    service, _hub = make_service(tmp_path)
    msgs = _json(service.snapshot_messages())
    assert _by_type(msgs, 'state')
    catalog = _by_type(msgs, 'catalog')[0]
    assert [p['program_id'] for p in catalog['programs']] == ['prog']


def test_state_published_only_on_change(tmp_path):
    service, _hub = make_service(tmp_path)
    first, _ = service.tick_once()             # initial state + catalog
    assert _by_type(_json(first), 'state')
    second, _ = service.tick_once()            # nothing changed
    assert _by_type(_json(second), 'state') == []

    cmd(service, 'load', program_id='prog')
    third, _ = service.tick_once()             # load changed the state
    assert _by_type(_json(third), 'state')


def test_member_attach_event_forwarded(tmp_path):
    service, hub = make_service(tmp_path)
    service.tick_once()                        # drain the initial state/catalog
    hub.emit(DeviceConnected(uid='sim-a', boot_token=7, rebooted=False))
    json_msgs, _ = service.tick_once()

    events = _by_type(_json(json_msgs), 'event')
    assert events and events[0]['event'] == 'member_attached'
    assert events[0]['uid'] == 'sim-a'


def test_catalog_resent_after_publish(tmp_path):
    service, _hub = make_service(tmp_path)
    service.tick_once()                        # clears the initial catalog flag
    after_idle, _ = service.tick_once()
    assert _by_type(_json(after_idle), 'catalog') == []

    assert cmd(service, 'publish', program_id='prog2', source=PROGRAM)['ok'] is True
    json_msgs, _ = service.tick_once()
    catalog = _by_type(_json(json_msgs), 'catalog')[0]
    assert {p['program_id'] for p in catalog['programs']} == {'prog', 'prog2'}


def test_state_includes_strip_layout(tmp_path):
    service, _hub = make_service(tmp_path)
    cmd(service, 'load', program_id='prog')
    state = _by_type(_json(service.snapshot_messages()), 'state')[0]
    assert state['session']['strips'] == [{'strip_id': 'main', 'length': 30}]


def test_configured_device_status_tracks_attachment(tmp_path):
    service, hub = make_service(tmp_path)
    device = _by_type(_json(service.snapshot_messages()), 'state')[0]['devices'][0]
    assert device['configured'] is True
    assert device['status'] == 'offline'

    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False))
    service.tick_once()
    device = _by_type(_json(service.snapshot_messages()), 'state')[0]['devices'][0]
    assert device['status'] == 'online'


def test_discovered_device_surfaced_as_stub(tmp_path):
    service, hub = make_service(tmp_path)
    hub.discovered = {'sim-new'}
    devices = _by_type(_json(service.snapshot_messages()), 'state')[0]['devices']
    discovered = [d for d in devices if d['uid'] == 'sim-new']
    assert discovered == [{'uid': 'sim-new', 'configured': False,
                           'status': 'discovered'}]


def test_configured_device_not_duplicated_when_also_discovered(tmp_path):
    service, hub = make_service(tmp_path)
    hub.discovered = {'sim-a'}   # the configured device is also broadcasting
    devices = _by_type(_json(service.snapshot_messages()), 'state')[0]['devices']
    sim_a = [d for d in devices if d['uid'] == 'sim-a']
    assert len(sim_a) == 1
    assert sim_a[0]['configured'] is True


# ---------------------------------------------------------------------------
# Device editing commands
# ---------------------------------------------------------------------------

def _devices(service):
    return _by_type(_json(service.snapshot_messages()), 'state')[0]['devices']


def test_add_device_persists_and_surfaces(tmp_path):
    service, _hub, config_path = make_service_with_config(tmp_path)
    reply = cmd(service, 'add_device', device_uid='sim-b', strip_id='side', length=30)
    assert reply['ok'] is True

    doc = json.loads(config_path.read_text())
    assert any(d['device_uid'] == 'sim-b' for d in doc['devices'])
    sim_b = [d for d in _devices(service) if d['uid'] == 'sim-b']
    assert sim_b and sim_b[0]['status'] == 'offline'


def test_add_device_ignores_device_type(tmp_path):
    service, _hub, config_path = make_service_with_config(tmp_path)
    reply = cmd(service, 'add_device', device_type='sim', device_uid='sim-b',
                strip_id='side', length=30)
    assert reply['ok'] is True
    sim_b = next(d for d in json.loads(config_path.read_text())['devices']
                 if d['device_uid'] == 'sim-b')
    assert 'device_type' not in sim_b


def test_add_device_allowed_while_a_program_is_loaded(tmp_path):
    # Adding a device after load is allowed: it persists and joins membership
    # parked DETACHED, so it cannot disturb the running program (it routes only
    # on the next load).
    service, _hub, config_path = make_service_with_config(tmp_path)
    cmd(service, 'load', program_id='prog')
    reply = cmd(service, 'add_device', device_uid='sim-b', strip_id='side', length=30)
    assert reply['ok'] is True
    assert any(d['device_uid'] == 'sim-b'
               for d in json.loads(config_path.read_text())['devices'])
    sim_b = next(d for d in _devices(service) if d['uid'] == 'sim-b')
    assert sim_b['target_intent'] == 'detached'


def test_edit_and_remove_rejected_while_a_program_is_loaded(tmp_path):
    service, _hub, _ = make_service_with_config(tmp_path)
    cmd(service, 'load', program_id='prog')
    edit = cmd(service, 'edit_device', target_device_uid='sim-a',
               device_uid='sim-a', strip_id='main', length=45)
    assert edit['ok'] is False
    assert 'before a program is loaded' in edit['error']
    remove = cmd(service, 'remove_device', device_uid='sim-a')
    assert remove['ok'] is False
    assert 'before a program is loaded' in remove['error']


def test_device_edit_without_config_path_errors(tmp_path):
    service, _hub = make_service(tmp_path)   # config_path is None
    reply = cmd(service, 'add_device', device_uid='sim-b', strip_id='side', length=30)
    assert reply['ok'] is False
    assert 'config file' in reply['error']


def test_add_device_invalid_input_changes_nothing(tmp_path):
    service, _hub, config_path = make_service_with_config(tmp_path)
    before = config_path.read_text()
    reply = cmd(service, 'add_device', device_uid='sim-b', strip_id='side', length=0)
    assert reply['ok'] is False
    assert config_path.read_text() == before
    assert service._session.member('sim-b') is None


def test_edit_device_preserves_label_when_omitted(tmp_path):
    service, _hub, config_path = make_service_with_config(tmp_path)
    cmd(service, 'add_device', device_uid='sim-b', strip_id='side', length=30,
        label='Stage')
    reply = cmd(service, 'edit_device', target_device_uid='sim-b',
                device_uid='sim-b', strip_id='side', length=45)
    assert reply['ok'] is True
    sim_b = next(d for d in json.loads(config_path.read_text())['devices']
                 if d['device_uid'] == 'sim-b')
    assert sim_b['label'] == 'Stage' and sim_b['length'] == 45


def test_remove_device_persists_and_disconnects(tmp_path):
    service, hub, config_path = make_service_with_config(tmp_path)
    reply = cmd(service, 'remove_device', device_uid='sim-a')
    assert reply['ok'] is True
    assert json.loads(config_path.read_text())['devices'] == []
    assert ('sim-a', 'removed') in hub.disconnected


def test_preview_frame_emitted_only_when_enabled(tmp_path):
    service, hub = make_service(tmp_path)
    drive_service_to_playing(service, hub)            # single strip 'main', 50 fps
    service._clock_us.now_us += 1_000_000             # live runs ahead of frames

    # No subscriber yet: the frame is dropped, nothing is assembled.
    hub.frames = [FramePreview(uid='sim-a', frame_index=999,
                               t_program=0.02, rgb=b'\x00' * 90)]
    _json_msgs, frame_msgs = service.tick_once()
    assert frame_msgs == []

    service.set_preview_enabled(True)
    hub.frames = [FramePreview(uid='sim-a', frame_index=1000,
                               t_program=0.04, rgb=b'\x00' * 90)]
    _json_msgs, frame_msgs = service.tick_once()
    assert len(frame_msgs) == 1
    assert frame_msgs[0][4] == KIND_FRAME             # the kind byte


def test_observer_role_unaffected_close(tmp_path):
    # close() must tear down the hub it owns.
    service, hub = make_service(tmp_path)
    service.close()
    assert hub.closed is True


# ---------------------------------------------------------------------------
# set_background: install a program as a device's local fallback animation
# ---------------------------------------------------------------------------

def _device_background(service, uid='sim-a'):
    state = _by_type(_json(service.snapshot_messages()), 'state')[0]
    return next(d['background'] for d in state['devices'] if d['uid'] == uid)


def _published_background(msgs, uid='sim-a'):
    """The sim-a background phase from a state message in tick_once output, or
    None if no state was published."""
    states = _by_type(_json(msgs), 'state')
    if not states:
        return None
    bg = next(d['background'] for d in states[-1]['devices'] if d['uid'] == uid)
    return bg['phase'] if bg else None


def _ack(hub, status, uid='sim-a'):
    _, on_ack = hub.last_send(uid)
    on_ack(AckMsg(status=status, payload=b''))


def test_set_background_stores_then_orders(tmp_path):
    service, hub = make_service(tmp_path)
    hub.connected.add('sim-a')

    reply = cmd(service, 'set_background', device_uid='sim-a', program_id='prog')
    assert reply['ok'] is True
    assert reply['result'] == {'device_uid': 'sim-a', 'program_id': 'prog',
                               'phase': 'storing'}
    assert hub.last_send('sim-a')[0] == wire.CMD_STORE_ANIMATION

    _ack(hub, wire.ACK_OK)                                  # store accepted
    assert hub.last_send('sim-a')[0] == wire.CMD_SET_ANIMATION_ORDER
    assert _device_background(service)['phase'] == 'ordering'

    _ack(hub, wire.ACK_OK)                                  # order accepted
    # No preview, so the chain ends at ready without a local play.
    assert _device_background(service) == {
        'program_id': 'prog', 'phase': 'ready', 'error': None}


def test_set_background_preview_also_plays_local(tmp_path):
    service, hub = make_service(tmp_path)
    hub.connected.add('sim-a')

    cmd(service, 'set_background', device_uid='sim-a', program_id='prog', preview=True)
    _ack(hub, wire.ACK_OK)                                  # store
    _ack(hub, wire.ACK_OK)                                  # order
    assert hub.last_send('sim-a')[0] == wire.CMD_PLAY_LOCAL_ANIMATION
    assert _device_background(service)['phase'] == 'starting'

    _ack(hub, wire.ACK_OK)                                  # play_local
    assert _device_background(service)['phase'] == 'ready'


def test_set_background_refusal_marks_failed(tmp_path):
    service, hub = make_service(tmp_path)
    hub.connected.add('sim-a')

    cmd(service, 'set_background', device_uid='sim-a', program_id='prog')
    sent_before = len(hub.sent)
    _ack(hub, wire.ACK_ERROR)                               # store refused

    bg = _device_background(service)
    assert bg['phase'] == 'failed' and 'store_animation' in bg['error']
    assert len(hub.sent) == sent_before                    # chain stopped


def test_set_background_rejects_unconnected_device(tmp_path):
    service, _hub = make_service(tmp_path)                  # sim-a not connected
    reply = cmd(service, 'set_background', device_uid='sim-a', program_id='prog')
    assert reply['ok'] is False and 'not connected' in reply['error']


def test_set_background_rejects_unknown_program(tmp_path):
    service, hub = make_service(tmp_path)
    hub.connected.add('sim-a')
    reply = cmd(service, 'set_background', device_uid='sim-a', program_id='nope')
    assert reply['ok'] is False and 'unknown program' in reply['error']


def test_set_background_rejects_concurrent_install(tmp_path):
    service, hub = make_service(tmp_path)
    hub.connected.add('sim-a')
    cmd(service, 'set_background', device_uid='sim-a', program_id='prog')   # in flight
    reply = cmd(service, 'set_background', device_uid='sim-a', program_id='prog')
    assert reply['ok'] is False and 'already in progress' in reply['error']


def test_set_background_phase_changes_are_published(tmp_path):
    # Each phase transition must survive the tick_once change gate, not get
    # masked by a shared mutable dict in _last_state.
    service, hub = make_service(tmp_path)
    hub.connected.add('sim-a')
    cmd(service, 'set_background', device_uid='sim-a', program_id='prog')

    assert _published_background(service.tick_once()[0]) == 'storing'
    _ack(hub, wire.ACK_OK)                                   # store -> ordering
    _ack(hub, wire.ACK_OK)                                   # ordering -> ready
    assert _published_background(service.tick_once()[0]) == 'ready'


def test_set_background_orphaned_by_disconnect_is_reaped(tmp_path):
    service, hub = make_service(tmp_path)
    hub.connected.add('sim-a')
    cmd(service, 'set_background', device_uid='sim-a', program_id='prog')   # storing

    hub.connected.discard('sim-a')                          # link drops, no ACK
    service.tick_once()                                     # reaper runs

    bg = _device_background(service)
    assert bg['phase'] == 'failed' and 'disconnected' in bg['error']

    # The wedge is cleared: a retry after reconnect is accepted, not rejected
    # as "already in progress".
    hub.connected.add('sim-a')
    assert cmd(service, 'set_background', device_uid='sim-a', program_id='prog')['ok']


def test_set_background_preview_rejected_while_device_is_serving(tmp_path):
    service, hub = make_service(tmp_path)
    drive_service_to_playing(service, hub)                  # sim-a serving 'prog'
    hub.connected.add('sim-a')

    reply = cmd(service, 'set_background', device_uid='sim-a',
                program_id='prog', preview=True)
    assert reply['ok'] is False and 'serving a loaded program' in reply['error']

    # Storing/ordering don't disrupt live playback, so a non-preview install is
    # still allowed.
    assert cmd(service, 'set_background', device_uid='sim-a', program_id='prog')['ok']
