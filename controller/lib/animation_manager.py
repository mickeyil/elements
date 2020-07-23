import array
import struct
from lib.utils import validate_in_list

from collections import namedtuple

STRIP_TYPES = {
    "STRIP_RGB": 0,
    "STRIP_BGR": 1,
}

ANIMATION_TYPES = {
    'fill': 1000,
}

AnimationSetupHeader = namedtuple('AnimationSetupHeader', ['id', 'anim_type'])
AnimationParamsHeader = namedtuple('AnimationParamsHeader',
                                   ['t_start', 'duration'])
AnimationFillParams = namedtuple('AnimationFillParams', ['slope', 'speed_factor'])

ANIMATION_SETUP_KEYS = ['type', 't_start', 'duration', 'params']

# specific animations
ANIMATION_FILL_SETUP_KEYS = ['color', 'slope', 'speed_factor']
COLOR_KEYS = ('h', 's', 'v')

def parse_animation(exec_time, seq_id, params):
    if not all(key in params for key in ANIMATION_SETUP_KEYS):
        raise ValueError('missing mandatory keys.')

    if not isinstance(exec_time, float):
        raise ValueError('invalid exec_time format')

    anim_type = params['type']
    if not anim_type in ANIMATION_TYPES:
        raise ValueError('invalid animation type')

    t_start = params['t_start']
    if not isinstance(t_start, float):
        raise ValueError('invalid t_start format')

    # add execution time (epoch in seconds.millisecs) to t_start
    t_start += exec_time

    duration = params['duration']
    if not isinstance(duration, float):
        raise ValueError('invalid duration format')
    if duration < 0.0:
        raise ValueError('negative duration value')

    anim_setup_header = AnimationSetupHeader(id=seq_id, anim_type=ANIMATION_TYPES[anim_type])
    setup_header_bytes = struct.pack('<Hh', *anim_setup_header)

    anim_params_header = AnimationParamsHeader(t_start=t_start, duration=duration)
    params_header_bytes = struct.pack('<df', *anim_params_header)
    header_bytes = setup_header_bytes + params_header_bytes
    specific_animation_parser = specific_animation_parsers[anim_type]

    msg_bytes = header_bytes + specific_animation_parser(params['params'])
    return msg_bytes


def parse_animation_fill(params):
    if not all(key in params for key in ANIMATION_FILL_SETUP_KEYS):
        raise ValueError('missing mandatory keys.')
    fill_params_header = AnimationFillParams(slope=params['slope'],
                                             speed_factor=params['speed_factor'])

    color = params['color']
    color_bytes = parse_color_value(color)

    fill_header_bytes = struct.pack('<ff', *fill_params_header) + color_bytes
    return fill_header_bytes


def parse_color_value(params):
    if not all(key in params for key in COLOR_KEYS):
        raise ValueError('missing mandatory keys.')
    h = params['h']
    s = params['s']
    v = params['v']
    msg_bytes = array.array('B', [h, s, v])
    return msg_bytes


specific_animation_parsers = {
    'fill' : parse_animation_fill,
}


class AnimationManager:
    def __init__(self, device_id, mclient):
        self.device_id = device_id
        self.mclient = mclient
        self.seq_id = 0
        self.plans = {}

    def setup(self, strip_type, strip_len):
        header_bytes = struct.pack("<BB", STRIP_TYPES[strip_type], strip_len)
        msg = header_bytes + array.array('B', list(range(strip_len)))
        topic = f"elements/{self.device_id}/operate/animation/setup"
        self.mclient.publish(topic, msg, 1, False)

    def load_plan(self, name, anim_list):
        self.plans[name] = anim_list

    def animate(self, exec_time, plan_name):
        topic = f"elements/{self.device_id}/operate/animation/add"
        for animation in self.plans[plan_name]:
            msg = parse_animation(exec_time, self.seq_id, animation)
            self.seq_id += 1
            if self.seq_id == 65000:
                self.seq_id = 0

            self.mclient.publish(topic, msg, 1, False)
