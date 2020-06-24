import array
import struct
from collections import namedtuple

STRIP_TYPES = {
    'rgb': 0,
    'bgr': 1,
}

ANIMATION_TYPES = {
    'fill': 1000,
}

CHANNEL_SETUP_KEYS = ('strip_type', 'active_indices')
ChannelSetupHeader = namedtuple('ChannelSetupHeader', ['strip_type', 'len'])
AnimationSetupHeader = namedtuple('AnimationSetupHeader',
                                  ['anim_id', 'anim_type', 't_start', 'duration'])

ANIMATION_SETUP_KEYS = ('type', 't_start', 'duration', 'params')

# specific animations
ANIMATION_FILL_SETUP_KEYS = ('color')
COLOR_KEYS = ('h', 's', 'v')

specific_animation_parsers = {
    'fill' : parse_animation_fill,
}


def parse_channel_setup(params):
    if not all(key in params for key in CHANNEL_SETUP_KEYS):
        raise ValueError('missing mandatory keys.')

    strip_type = params['strip_type']
    if not strip_type in STRIP_TYPES:
        raise ValueError(f'invalid strip type: {strip_type}')

    indices = params['active_indices']
    if not all(isinstance(x, int) for x in indices):
        raise ValueError('active_indices field must contain only integers')

    header = ChannelSetupHeader(strip_type=strip_type, active_indices=len(indices))
    header_bytes = struct.pack("<BB", *header)
    msg = header_bytes + array.array('B', indices)
    return msg


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

    header = AnimationSetupHeader(anim_id=seq_id, anim_type=anim_type,
                                  t_start=t_start, duration=duration)
    header_bytes = struct.pack('<HHdf', *header)
    specific_animation_parser = specific_animation_parsers[anim_type]

    msg_bytes = header_bytes + specific_animation_parser(params['params'])
    return msg_bytes


def parse_animation_fill(params):
    if not all(key in params for key in ANIMATION_FILL_SETUP_KEYS):
        raise ValueError('missing mandatory keys.')
    color = params['color']
    color_bytes = parse_color_value(color)

    return color_bytes


def parse_color_value(params):
    if not all(key in params for key in COLOR_KEYS):
        raise ValueError('missing mandatory keys.')
    h = params['h']
    s = params['s']
    v = params['v']
    msg_bytes = array.array('B', [h, s, v])
    return msg_bytes


class Parser:

    def __init__(self):
        self.seq_id = 0

    def parse(self, exec_time, plan_instructions):
        ''' parses an array of animation instructions.
            returns byte array ready to be sent over mqtt.

            exec_time: execution time in double precision floating point.
                       accurate to the millisecond.
            plan_data: array of dictionaries to parse. dictionary key calls
                       the proper parser to its value.
            '''

        for plan in plan_data:
            pass