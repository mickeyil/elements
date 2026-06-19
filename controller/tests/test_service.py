"""ControllerService tests: command dispatch, publishing, and the load path.

Socket-free: a FakeHub stands in for the device hub and the service is driven
through handle_cmd()/tick_once()/snapshot_messages() directly. The load path
compiles a real (tiny) program against the configured topology.
"""

from elemctl.config import Config, DeviceConfig
from elemctl.controller_protocol import KIND_FRAME, KIND_JSON, parse_json_payload
from elemctl.hub import DeviceConnected, HubPoll
from elemctl.library import ProgramLibrary
from elemctl.service import ControllerService
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
        self.closed = False

    def emit(self, *events):
        self.pending_events.extend(events)

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
