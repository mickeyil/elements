import struct

from lib.utils import get_hex_str
from collections import namedtuple

EVENT_TYPES = {
    'in_range'  : 0,
}

SUPPRESS_TIME_MIN = 0.0
SUPPRESS_TIME_MAX = 10000.0

EVENT_SETUP_KEYS = ['event_type', 'event_id', 'sensor_id', 'suppress_time', 'polarity',
                    'sampling_window_size', 'report_topic_len']
EVENT_MANDATORY_KEYS = list(set(EVENT_SETUP_KEYS) - set(['event_id', 'report_topic_len'])) + ['topic', 'params']
IN_RANGE_PARAMS_KEYS = ['min', 'max', 'confidence']

EventSetupHeader = namedtuple('EventSetupHeader', EVENT_SETUP_KEYS)
EventInRangeParams = namedtuple('EventInRangeParams', IN_RANGE_PARAMS_KEYS)

def parse_event_inrange(params):
    if not all(key in params for key in IN_RANGE_PARAMS_KEYS):
        raise ValueError('missing mandatory keys.')

    inrage_params = EventInRangeParams(min=params['min'],
                                       max=params['max'],
                                       confidence=params['confidence'])
    inrange_params_bytes = struct.pack('<HHf', *inrage_params)
    return inrange_params_bytes


# specific parsers
specific_event_parsers = {
    'in_range' : parse_event_inrange,
}

def parse_event_config(params, event_id):
    if not all(key in params for key in EVENT_MANDATORY_KEYS):
        raise ValueError('missing mandatory keys.')

    if params['event_type'] not in EVENT_TYPES.keys():
        raise ValueError('invalid event type')

    if params['suppress_time'] < SUPPRESS_TIME_MIN or \
        params['suppress_time'] > SUPPRESS_TIME_MAX:
        raise ValueError('suppress_time out of bounds')

    event_header = EventSetupHeader(event_type=EVENT_TYPES[params['event_type']],
                                    event_id=event_id,
                                    sensor_id=params['sensor_id'],
                                    suppress_time=params['suppress_time'],
                                    polarity=params['polarity'],
                                    sampling_window_size=params['sampling_window_size'],
                                    report_topic_len=len(params['topic']))

    event_header_bytes = struct.pack('<HHHfBBB', *event_header) + params['topic'].encode('ascii')

    return event_header_bytes + specific_event_parsers[params['event_type']](params['params'])
