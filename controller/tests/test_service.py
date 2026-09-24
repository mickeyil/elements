"""ControllerService tests: command dispatch, publishing, and the load path.

Socket-free: a FakeHub stands in for the device hub and the service is driven
through handle_cmd()/tick_once()/snapshot_messages() directly. The load path
compiles a real (tiny) program against the configured topology.
"""

import json
import struct

from elemctl.config import Config, DeviceConfig, load_config_obj
from elemctl.controller_protocol import (
    KIND_DEVICE_FRAME,
    KIND_FRAME,
    KIND_JSON,
    parse_device_frame_payload,
    parse_json_payload,
)
from elemctl.device_status import STATUS_QUERY_INTERVAL_US
from elemctl.hub import DeviceConnected, DeviceDisconnected, DeviceInfo, HubPoll
from elemctl.library import ProgramLibrary
from elemctl.service import (
    FIRMWARE_CHECK_INTERVAL_US,
    OTA_PROGRESS_INTERVAL_US,
    ControllerService,
    hsv_to_rgb,
)
from elemctl.session import Intent
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
        self.status_queries = []   # (uid, on_ack) per status poll
        self.discovered = set()
        self.info = {}             # uid -> DeviceInfo, as DISCOVER would record
        self.connected = set()
        self.disconnected = []
        self.closed = False

    def is_connected(self, uid):
        return uid in self.connected

    def emit(self, *events):
        self.pending_events.extend(events)

    def discovered_uids(self):
        return set(self.discovered)

    def device_info(self, uid):
        return self.info.get(uid)

    def disconnect(self, uid, reason='removed'):
        self.disconnected.append((uid, reason))

    def poll(self, wanted_uids):
        events, self.pending_events = self.pending_events, []
        frames, self.frames = self.frames, []
        return HubPoll(events=events, frames=frames)

    def send(self, uid, encoded, on_ack=None):
        # Status polls run beside the session's commands; keep them apart so
        # sent / last_send() stay the reconciler's commands.
        if encoded[4] == wire.CMD_QUERY_DEVICE_STATUS:
            self.status_queries.append((uid, on_ack))
        else:
            self.sent.append((uid, encoded, on_ack))
        return True

    def last_send(self, uid=None):
        for u, encoded, on_ack in reversed(self.sent):
            if uid is None or u == uid:
                return encoded[4], on_ack
        return None, None

    def close(self):
        self.closed = True


def make_service(tmp_path, with_program=True, ota_factory=None, devices=None):
    if with_program:
        (tmp_path / 'prog.py').write_text(PROGRAM)
    # The image path is pinned inside tmp_path so the repo's own build
    # output never leaks into a test.
    config = Config(discovery_port=6040, link_port=6041, frame_port=6042,
                    sync_port=6043, log_port=6044,
                    devices=devices or [DeviceConfig('sim-a', 'main', 30)],
                    animations_dir=str(tmp_path),
                    firmware_image=str(tmp_path / 'fw' / 'firmware.bin'))
    hub = FakeHub()
    service = ControllerService(config, hub=hub, clock_us=FakeClock(),
                                library=ProgramLibrary(str(tmp_path)),
                                ota_factory=ota_factory)
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


def test_load_reports_loop_off_by_default(tmp_path):
    service, _hub = make_service(tmp_path)
    cmd(service, 'load', program_id='prog')
    state = _by_type(_json(service.snapshot_messages()), 'state')[0]
    assert state['session']['loop'] is False


def test_load_forever_program_is_thirty_days_and_loops(tmp_path):
    service, _hub = make_service(tmp_path, with_program=False)
    (tmp_path / 'ever.py').write_text(
        "from elements.dsl import forever, strip, paint\n"
        "BEAT = 1.0\n"
        "DURATION = forever\n"
        "main = strip('main')\n"
        "paint(color='white').schedule(main.pixels('0'), at=0, duration=forever)\n"
    )
    cmd(service, 'rescan')
    reply = cmd(service, 'load', program_id='ever')
    assert reply['ok'] is True, reply
    state = _by_type(_json(service.snapshot_messages()), 'state')[0]
    assert state['session']['loop'] is True
    assert state['session']['duration'] == 30 * 24 * 3600


def test_load_loop_literal_passes_through(tmp_path):
    service, _hub = make_service(tmp_path, with_program=False)
    (tmp_path / 'looped.py').write_text("LOOP = True\n" + PROGRAM)
    cmd(service, 'rescan')
    assert cmd(service, 'load', program_id='looped')['ok'] is True
    state = _by_type(_json(service.snapshot_messages()), 'state')[0]
    assert state['session']['loop'] is True
    assert state['session']['duration'] == 4.0


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


def test_state_strip_length_is_the_configured_length(tmp_path):
    # Preview frames carry each strip's whole configured length, so observers
    # split them by that, not by a shorter authored length.
    service, _hub = make_service(tmp_path)
    source = PROGRAM.replace("strip('main')", "strip('main', 10)")
    assert cmd(service, 'publish', program_id='short', source=source)['ok'] is True
    assert cmd(service, 'load', program_id='short')['ok'] is True
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


def _device_state(service, uid='sim-a'):
    devices = _by_type(_json(service.snapshot_messages()), 'state')[0]['devices']
    return next(d for d in devices if d['uid'] == uid)


def _status_reply(synced, skew_us):
    flags = wire.STATUS_FLAG_CLOCK_SYNCED if synced else 0
    return AckMsg(status=wire.ACK_OK,
                  payload=struct.pack('<BBi', 0,  # mode: attached_controlled
                                      flags, skew_us))


def test_device_clock_is_null_until_the_status_reply(tmp_path):
    service, hub = make_service(tmp_path)
    assert _device_state(service)['clock_synced'] is None
    assert _device_state(service)['clock_skew_ms'] is None

    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False))
    service.tick_once()
    assert [uid for uid, _ in hub.status_queries] == ['sim-a']
    assert _device_state(service)['clock_synced'] is None

    _, on_ack = hub.status_queries[-1]
    on_ack(_status_reply(synced=True, skew_us=-420))
    device = _device_state(service)
    assert device['clock_synced'] is True
    assert device['clock_skew_ms'] == pytest.approx(-0.42)


def test_device_status_is_polled_on_an_interval(tmp_path):
    service, hub = make_service(tmp_path)
    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False))
    service.tick_once()
    hub.status_queries[-1][1](_status_reply(synced=False, skew_us=0))
    assert _device_state(service)['clock_synced'] is False

    service._clock_us.now_us += STATUS_QUERY_INTERVAL_US - 1
    service.tick_once()
    assert len(hub.status_queries) == 1          # not yet due

    service._clock_us.now_us += 1
    service.tick_once()
    assert len(hub.status_queries) == 2
    hub.status_queries[-1][1](_status_reply(synced=True, skew_us=150))
    assert _device_state(service)['clock_skew_ms'] == pytest.approx(0.15)


def test_unanswered_status_query_is_not_repeated(tmp_path):
    service, hub = make_service(tmp_path)
    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False))
    service.tick_once()
    service._clock_us.now_us += 3 * STATUS_QUERY_INTERVAL_US
    service.tick_once()
    assert len(hub.status_queries) == 1          # one in flight at a time


def test_device_clock_clears_on_detach_and_ignores_the_old_link(tmp_path):
    service, hub = make_service(tmp_path)
    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False))
    service.tick_once()
    hub.status_queries[-1][1](_status_reply(synced=True, skew_us=100))

    service._clock_us.now_us += STATUS_QUERY_INTERVAL_US
    service.tick_once()
    _, stale_on_ack = hub.status_queries[-1]     # in flight when the link drops

    hub.emit(DeviceDisconnected(uid='sim-a', reason='eof'))
    service.tick_once()
    assert _device_state(service)['clock_synced'] is None
    assert _device_state(service)['clock_skew_ms'] is None

    # A reconnect queries afresh; a late reply from the old link is dropped.
    hub.emit(DeviceConnected(uid='sim-a', boot_token=2, rebooted=True))
    service.tick_once()
    stale_on_ack(_status_reply(synced=True, skew_us=999))
    assert _device_state(service)['clock_skew_ms'] is None
    hub.status_queries[-1][1](_status_reply(synced=True, skew_us=0))
    assert _device_state(service)['clock_skew_ms'] == 0


def test_bad_status_reply_keeps_the_last_report(tmp_path):
    service, hub = make_service(tmp_path)
    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False))
    service.tick_once()
    hub.status_queries[-1][1](_status_reply(synced=True, skew_us=200))

    service._clock_us.now_us += STATUS_QUERY_INTERVAL_US
    service.tick_once()
    hub.status_queries[-1][1](AckMsg(status=wire.ACK_OK, payload=b'\x00'))
    assert _device_state(service)['clock_skew_ms'] == pytest.approx(0.2)

    # And the poll continues after it.
    service._clock_us.now_us += STATUS_QUERY_INTERVAL_US
    service.tick_once()
    assert len(hub.status_queries) == 3


def test_discovered_device_surfaced_as_stub(tmp_path):
    service, hub = make_service(tmp_path)
    hub.discovered = {'sim-new'}
    devices = _by_type(_json(service.snapshot_messages()), 'state')[0]['devices']
    discovered = [d for d in devices if d['uid'] == 'sim-new']
    assert discovered == [{'uid': 'sim-new', 'configured': False,
                           'status': 'discovered', 'version': None, 'ip': None,
                           'update_available': False}]


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


def test_edit_device_applies_strip_and_length_to_the_strip_group(tmp_path):
    service, _hub, config_path = make_service_with_config(tmp_path)
    cmd(service, 'add_device', device_uid='sim-twin', strip_id='main', length=30)
    cmd(service, 'add_device', device_uid='sim-c', strip_id='side', length=10)

    reply = cmd(service, 'edit_device', target_device_uid='sim-a',
                device_uid='sim-a', strip_id='halo', length=45)

    assert reply['ok'] is True
    doc = {d['device_uid']: d for d in json.loads(config_path.read_text())['devices']}
    assert (doc['sim-twin']['strip_id'], doc['sim-twin']['length']) == ('halo', 45)
    assert (doc['sim-c']['strip_id'], doc['sim-c']['length']) == ('side', 10)
    session = service._session
    for uid in ('sim-a', 'sim-twin'):
        assert (session.member(uid).strip_id, session.member(uid).strip_length) == ('halo', 45)
    assert (session.member('sim-c').strip_id, session.member('sim-c').strip_length) == ('side', 10)


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
    hub.frames = [FramePreview(uid='sim-a', frame_index=999, cycle=0,
                               t_ms=20, rgb=b'\x00' * 90)]
    _json_msgs, frame_msgs = service.tick_once()
    assert frame_msgs == []

    service.set_preview_enabled(True)
    hub.frames = [FramePreview(uid='sim-a', frame_index=1000, cycle=0,
                               t_ms=40, rgb=b'\x00' * 90)]
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


# ---------------------------------------------------------------------------
# Firmware: versions, the available image, and update_firmware
# ---------------------------------------------------------------------------

ESP = 'esp-aabbccddeeff'


class FakeOta:
    """Stands in for ota.FirmwareUpdate: records its arguments and serves
    whatever snapshot the test sets."""

    instances = []

    def __init__(self, *, uid, device_ip, image_path, listen_port, password):
        self.kwargs = dict(uid=uid, device_ip=device_ip, image_path=image_path,
                           listen_port=listen_port, password=password)
        self.started = False
        self.state = {'uid': uid, 'phase': 'inviting', 'bytes_sent': 0,
                      'total_bytes': 0, 'error': None,
                      'started_at': None, 'finished_at': None}
        FakeOta.instances.append(self)

    def start(self):
        self.started = True

    def snapshot(self):
        return dict(self.state)


@pytest.fixture
def fake_ota():
    FakeOta.instances = []
    return FakeOta


def _write_image(tmp_path, version='0.7'):
    fw = tmp_path / 'fw'
    fw.mkdir(exist_ok=True)
    (fw / 'firmware.bin').write_bytes(b'\xe9' + b'\x00' * 99)
    if version is not None:
        (fw / 'firmware.version').write_text(version + '\n')


def _state(service):
    return _by_type(_json(service.snapshot_messages()), 'state')[0]


def _device(service, uid):
    return next(d for d in _state(service)['devices'] if d['uid'] == uid)


def _elapse(service, us):
    service._clock_us.now_us += us


def test_device_version_and_ip_come_from_discovery(tmp_path):
    service, hub = make_service(tmp_path)
    assert _device(service, 'sim-a')['version'] is None
    assert _device(service, 'sim-a')['ip'] is None

    hub.info['sim-a'] = DeviceInfo(ip='127.0.0.1', version='e029971+d', seen_us=1)
    hub.info[ESP] = DeviceInfo(ip='10.0.0.9', version='', seen_us=1)
    hub.discovered = {ESP}
    assert _device(service, 'sim-a')['version'] == 'e029971+d'
    assert _device(service, 'sim-a')['ip'] == '127.0.0.1'
    # A legacy device reports no version: '' (known, empty), not None.
    assert _device(service, ESP) == {'uid': ESP, 'configured': False,
                                     'status': 'discovered', 'version': '',
                                     'ip': '10.0.0.9', 'update_available': False}


def test_firmware_block_without_an_image(tmp_path):
    service, _hub = make_service(tmp_path)
    assert _state(service)['firmware'] == {
        'available_version': None, 'image_present': False, 'update': None}


def test_firmware_block_reads_the_version_sidecar(tmp_path):
    _write_image(tmp_path, '0.7+d')
    service, _hub = make_service(tmp_path)
    firmware = _state(service)['firmware']
    assert firmware['available_version'] == '0.7+d'
    assert firmware['image_present'] is True


def test_firmware_sidecar_rechecked_on_an_interval(tmp_path):
    service, _hub = make_service(tmp_path)
    service.tick_once()
    _write_image(tmp_path, '0.8')

    # Within the interval the disk is not consulted again.
    service.tick_once()
    assert _state(service)['firmware']['available_version'] is None

    _elapse(service, FIRMWARE_CHECK_INTERVAL_US)
    json_msgs, _ = service.tick_once()
    published = _by_type(_json(json_msgs), 'state')
    assert published and published[0]['firmware']['available_version'] == '0.8'
    assert published[0]['firmware']['image_present'] is True


def test_update_available_is_decided_per_device(tmp_path):
    service, hub = make_service(tmp_path)
    hub.discovered = {ESP}
    hub.info[ESP] = DeviceInfo(ip='10.0.0.9', version='0.6', seen_us=1)
    hub.info['sim-a'] = DeviceInfo(ip='127.0.0.1', version='e029971', seen_us=1)
    # No image built: nothing to offer anyone.
    assert _device(service, ESP)['update_available'] is False
    assert _device(service, 'sim-a')['update_available'] is False

    _write_image(tmp_path, '0.7')
    _elapse(service, FIRMWARE_CHECK_INTERVAL_US)
    service.tick_once()
    assert _device(service, ESP)['update_available'] is True
    # A sim is never offered one, whatever its version says.
    assert _device(service, 'sim-a')['update_available'] is False

    # Current or newer than the image: no offer.
    hub.info[ESP] = DeviceInfo(ip='10.0.0.9', version='0.7', seen_us=1)
    assert _device(service, ESP)['update_available'] is False
    hub.info[ESP] = DeviceInfo(ip='10.0.0.9', version='0.10', seen_us=1)
    assert _device(service, ESP)['update_available'] is False
    # Firmware that predates version reporting cannot be compared: offered.
    hub.info[ESP] = DeviceInfo(ip='10.0.0.9', version='', seen_us=1)
    assert _device(service, ESP)['update_available'] is True


def test_update_available_needs_a_discovered_address(tmp_path):
    # An ESP with no DeviceInfo (in practice a configured one that has never
    # broadcast) has no address to send to, so it is not offered an update
    # even though an image is waiting. Configured and discovered devices share
    # the decision; a discovered stub is the ESP this fixture can list.
    _write_image(tmp_path)
    service, hub = make_service(tmp_path)
    hub.discovered = {ESP}
    assert _device(service, ESP)['update_available'] is False


def test_update_firmware_starts_a_transfer_to_the_discovered_address(tmp_path, fake_ota):
    _write_image(tmp_path)
    service, hub = make_service(tmp_path, ota_factory=fake_ota)
    hub.info[ESP] = DeviceInfo(ip='10.0.0.9', version='0.6', seen_us=1)

    reply = cmd(service, 'update_firmware', uid=ESP)
    assert reply['ok'] is True and reply['result'] == {}
    (runner,) = fake_ota.instances
    assert runner.started
    assert runner.kwargs['device_ip'] == '10.0.0.9'
    assert runner.kwargs['listen_port'] == service._config.ota_port
    assert runner.kwargs['password'] is None
    assert str(runner.kwargs['image_path']).endswith('firmware.bin')
    assert _state(service)['firmware']['update']['phase'] == 'inviting'


def test_update_firmware_refusals(tmp_path, fake_ota):
    service, hub = make_service(tmp_path, ota_factory=fake_ota)

    reply = cmd(service, 'update_firmware')
    assert reply['ok'] is False and 'requires a uid' in reply['error']

    reply = cmd(service, 'update_firmware', uid=ESP)       # never broadcast
    assert reply['ok'] is False and 'no known address' in reply['error']

    hub.info[ESP] = DeviceInfo(ip='10.0.0.9', version='0.6', seen_us=1)
    reply = cmd(service, 'update_firmware', uid=ESP)       # no image built
    assert reply['ok'] is False and 'image not found' in reply['error']

    _write_image(tmp_path)
    hub.info['sim-a'] = DeviceInfo(ip='127.0.0.1', version='abc', seen_us=1)
    reply = cmd(service, 'update_firmware', uid='sim-a')
    assert reply['ok'] is False and 'sim' in reply['error']
    assert fake_ota.instances == []


@pytest.mark.parametrize('device_version', ['0.7', '0.8', '1.0'])
def test_update_firmware_refused_when_no_update_is_available(tmp_path, fake_ota,
                                                             device_version):
    # The service is the authority: a device already on the image's version,
    # or newer, is refused even if a client asks anyway.
    _write_image(tmp_path, '0.7')
    service, hub = make_service(tmp_path, ota_factory=fake_ota)
    hub.info[ESP] = DeviceInfo(ip='10.0.0.9', version=device_version, seen_us=1)
    reply = cmd(service, 'update_firmware', uid=ESP)
    assert reply['ok'] is False and 'no firmware update available' in reply['error']
    assert fake_ota.instances == []


def test_update_firmware_accepts_a_dirty_image_over_the_same_version(tmp_path, fake_ota):
    _write_image(tmp_path, '0.7+d')
    service, hub = make_service(tmp_path, ota_factory=fake_ota)
    hub.info[ESP] = DeviceInfo(ip='10.0.0.9', version='0.7+d', seen_us=1)
    assert cmd(service, 'update_firmware', uid=ESP)['ok']
    assert len(fake_ota.instances) == 1


def test_update_firmware_refused_while_one_runs(tmp_path, fake_ota):
    _write_image(tmp_path)
    service, hub = make_service(tmp_path, ota_factory=fake_ota)
    hub.info[ESP] = DeviceInfo(ip='10.0.0.9', version='0.6', seen_us=1)
    assert cmd(service, 'update_firmware', uid=ESP)['ok']

    fake_ota.instances[0].state['phase'] = 'sending'
    reply = cmd(service, 'update_firmware', uid=ESP)
    assert reply['ok'] is False and 'already running' in reply['error']

    # Once the transfer ends (either way) the next one is accepted.
    fake_ota.instances[0].state['phase'] = 'failed'
    assert cmd(service, 'update_firmware', uid=ESP)['ok']
    assert len(fake_ota.instances) == 2


def test_update_progress_is_throttled_but_outcome_is_immediate(tmp_path, fake_ota):
    _write_image(tmp_path)
    service, hub = make_service(tmp_path, ota_factory=fake_ota)
    hub.info[ESP] = DeviceInfo(ip='10.0.0.9', version='0.6', seen_us=1)
    assert cmd(service, 'update_firmware', uid=ESP)['ok']
    runner = fake_ota.instances[0]
    service.tick_once()

    runner.state.update(phase='sending', total_bytes=4096)
    json_msgs, _ = service.tick_once()                     # phase change: now
    update = _by_type(_json(json_msgs), 'state')[0]['firmware']['update']
    assert update['phase'] == 'sending' and update['bytes_sent'] == 0

    runner.state['bytes_sent'] = 1024
    json_msgs, _ = service.tick_once()                     # progress: held back
    assert _by_type(_json(json_msgs), 'state') == []

    _elapse(service, OTA_PROGRESS_INTERVAL_US)
    json_msgs, _ = service.tick_once()
    update = _by_type(_json(json_msgs), 'state')[0]['firmware']['update']
    assert update['bytes_sent'] == 1024

    runner.state.update(phase='failed', error='device rejected the image: md5')
    json_msgs, _ = service.tick_once()
    update = _by_type(_json(json_msgs), 'state')[0]['firmware']['update']
    assert update['phase'] == 'failed' and 'md5' in update['error']

    # Nothing further changes, so nothing further is published.
    json_msgs, _ = service.tick_once()
    assert _by_type(_json(json_msgs), 'state') == []


# ---------------------------------------------------------------------------
# Operator panel: fill / run / stop one strip
# ---------------------------------------------------------------------------

# A panel-library program: written for whichever strip TARGET names.
GLOW = (
    "from elements.dsl import forever, strip, paint\n"
    "BEAT = 1.0\n"
    "DURATION = forever\n"
    "main = strip(TARGET)\n"
    "paint(color='white').schedule(main.pixels('0'), at=0, duration=forever)\n"
)

ONCE = (
    "from elements.dsl import sec, strip, paint\n"
    "BEAT = 1.0\n"
    "DURATION = 4.0\n"
    "main = strip(TARGET)\n"
    "paint(color='white').schedule(main.pixels('0'), at=0, duration=sec(4))\n"
)

PAIR = (
    "from elements.dsl import forever, strip, paint\n"
    "BEAT = 1.0\n"
    "DURATION = forever\n"
    "main = strip(TARGET)\n"
    "other = strip('other', length=5)\n"
    "paint(color='white').schedule(main.pixels('0'), at=0, duration=forever)\n"
    "paint(color='white').schedule(other.pixels('0'), at=0, duration=forever)\n"
)


def write_library(tmp_path, **programs):
    library = tmp_path / 'library'
    library.mkdir(exist_ok=True)
    for program_id, source in programs.items():
        (library / f'{program_id}.py').write_text(source)


def _panel_device(service, uid='sim-a'):
    state = _by_type(_json(service.snapshot_messages()), 'state')[0]
    return next(d for d in state['devices'] if d['uid'] == uid)


def test_hsv_to_rgb_follows_the_firmware_rules():
    # src/core/colors.cpp: hue wraps into [0, 360), s and v clamp to [0, 1],
    # s = 0 is grey at v, and each channel rounds half up.
    assert hsv_to_rgb(120, 1, 1) == (0, 255, 0)
    assert hsv_to_rgb(480, 1, 1) == hsv_to_rgb(-240, 1, 1) == (0, 255, 0)
    assert hsv_to_rgb(360, 1, 1) == (255, 0, 0)
    assert hsv_to_rgb(float('nan'), 1, 1) == (255, 0, 0)
    assert hsv_to_rgb(240, 2, 3) == (0, 0, 255)
    assert hsv_to_rgb(200, -1, 0.5) == (128, 128, 128)
    assert hsv_to_rgb(330, 0.5, 1) == (255, 128, 191)


def test_panel_fill_holds_the_color_on_the_whole_strip(tmp_path):
    service, hub = make_service(tmp_path)
    reply = cmd(service, 'panel_fill', strip_id='main', h=120, s=1, v=1)
    assert reply['ok'] is True

    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False))
    service.tick_once(); _ack_ok(hub)                  # SET_PROFILE
    service.tick_once()
    assert hub.sent[-1][1] == wire.encode_manual(bytes((0, 255, 0)) * 30)


def test_panel_commands_refuse_bad_input(tmp_path):
    service, _hub = make_service(tmp_path)
    assert cmd(service, 'panel_fill', strip_id='nope', h=0, s=1, v=1)['ok'] is False
    assert cmd(service, 'panel_fill', strip_id='main', h='red', s=1, v=1)['ok'] is False
    assert cmd(service, 'panel_run', strip_id='main', program_id='nope')['ok'] is False
    assert cmd(service, 'panel_stop', strip_id='nope')['ok'] is False
    assert service._session.member('sim-a').owner == 'show'


def test_panel_run_compiles_the_library_program_per_strip(tmp_path):
    service, _hub = make_service(tmp_path, devices=[
        DeviceConfig('sim-a', 'main', 30), DeviceConfig('sim-b', 'side', 12)])
    write_library(tmp_path, glow=GLOW)
    cmd(service, 'rescan')

    assert cmd(service, 'panel_run', strip_id='main', program_id='glow')['ok'] is True
    assert cmd(service, 'panel_run', strip_id='side', program_id='glow')['ok'] is True
    target = service._session.member('sim-a').target
    assert target.intent is Intent.PLAYING
    assert target.program_token == ('panel', 'glow', 'main')

    # TARGET bound each compile to its own strip, cached apart.
    source_hash = service._panel_library.get('glow').source_hash
    main = service._artifact_cache.get(source_hash, 'panel:main:30')
    side = service._artifact_cache.get(source_hash, 'panel:side:12')
    assert [(a.strip_id, a.length) for a in main.strips.values()] == [('main', 30)]
    assert [(a.strip_id, a.length) for a in side.strips.values()] == [('side', 12)]
    assert target.blob is main.strips['main'].blob

    cmd(service, 'panel_run', strip_id='main', program_id='glow')
    assert service._session.member('sim-a').target.blob is target.blob   # a cache hit


def test_panel_run_refuses_non_looping_or_multi_strip_programs(tmp_path):
    service, _hub = make_service(tmp_path)
    write_library(tmp_path, once=ONCE, pair=PAIR)
    cmd(service, 'rescan')
    for program_id in ('once', 'pair'):
        reply = cmd(service, 'panel_run', strip_id='main', program_id=program_id)
        assert reply['ok'] is False
        assert 'strip(TARGET), loop' in reply['error']
    assert service._session.member('sim-a').owner == 'show'


def test_catalog_lists_the_panel_library(tmp_path):
    service, _hub = make_service(tmp_path)
    write_library(tmp_path, glow=GLOW, broken='BEAT = 1.0\n')
    cmd(service, 'rescan')
    catalog = _by_type(_json(service.snapshot_messages()), 'catalog')[0]
    assert [p['program_id'] for p in catalog['programs']] == ['prog']
    assert catalog['library'] == [{'program_id': 'broken', 'error': 'missing DURATION'},
                                  {'program_id': 'glow', 'error': None}]


def test_state_shows_panel_ownership_and_mode(tmp_path):
    service, _hub = make_service(tmp_path)
    write_library(tmp_path, glow=GLOW)
    cmd(service, 'rescan')
    device = _panel_device(service)
    assert (device['owner'], device['panel']) == ('show', None)

    cmd(service, 'panel_fill', strip_id='main', h=30, s=0.5, v=0.25)
    device = _panel_device(service)
    assert device['owner'] == 'panel'
    assert device['panel'] == {'mode': 'manual', 'program_id': None, 'hsv': [30, 0.5, 0.25]}

    cmd(service, 'panel_run', strip_id='main', program_id='glow')
    assert _panel_device(service)['panel'] == {'mode': 'program', 'program_id': 'glow',
                                               'hsv': None}
    cmd(service, 'panel_stop', strip_id='main')
    assert _panel_device(service)['panel'] == {'mode': 'stopped', 'program_id': None,
                                               'hsv': None}

    cmd(service, 'load', program_id='prog')           # the show reclaims the strip
    device = _panel_device(service)
    assert (device['owner'], device['panel']) == ('show', None)


def test_panel_device_frame_is_published(tmp_path):
    service, hub = make_service(tmp_path)
    cmd(service, 'panel_fill', strip_id='main', h=0, s=1, v=1)
    hub.emit(DeviceConnected(uid='sim-a', boot_token=1, rebooted=False))
    service.tick_once(); _ack_ok(hub)                  # SET_PROFILE
    service.tick_once(); _ack_ok(hub)                  # MANUAL
    service.set_preview_enabled(True)

    rgb = bytes((255, 0, 0)) * 30
    hub.frames = [FramePreview(uid='sim-a', frame_index=1, cycle=0, t_ms=0, rgb=rgb)]
    _json_msgs, frame_msgs = service.tick_once()
    assert len(frame_msgs) == 1
    assert frame_msgs[0][4] == KIND_DEVICE_FRAME
    assert parse_device_frame_payload(frame_msgs[0][5:]) == ('sim-a', rgb)
