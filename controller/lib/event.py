import logging
import struct

from abc import ABC, abstractmethod
from collections import namedtuple

from lib.utils import get_hex_str, is_in_range, validate_all_exist

# limits
SUPPRESS_TIME_RANGE = [0.0, 10000.0]
POLARITY_VALUES = [0, 1]
SAMPLING_WINDOW_RANGE = [1, 64]

EVENT_TYPES = {
    'in_range': 0,
}

# event header members that will are parsed to binary members.
# note: order must match the c++ struct order.
EVENT_HEADER_MEMBERS = ['event_type', 'event_id', 'sensor_id', 'suppress_time', 'polarity',
                        'sampling_window_size', 'report_topic_len']

# mandatory keys that need to appear in params
EVENT_MANDATORY_KEYS = list(set(EVENT_HEADER_MEMBERS) - {'event_id', 'report_topic_len', 'sensor_id'}) \
                       + ['topic', 'params']

EventSetupHeader = namedtuple('EventSetupHeader', EVENT_HEADER_MEMBERS)


# validates event parameters. ensures all relevant keys exist in params dictionary,
# and sanity-checks values
def validate_event_params(params):
    validate_all_exist(params.keys(), EVENT_MANDATORY_KEYS)

    if params['event_type'] not in EVENT_TYPES.keys():
        raise ValueError('invalid event type')

    if not is_in_range(params['suppress_time'], SUPPRESS_TIME_RANGE):
        raise ValueError('suppress_time out of bounds')

    if params['polarity'] not in POLARITY_VALUES:
        raise ValueError('invalid polarity value')

    if not is_in_range(params['sampling_window_size'], SAMPLING_WINDOW_RANGE):
        raise ValueError('sampling_window_size out of range')

    if len(params['topic']) == 0:
        raise ValueError('empty topic')


def event_header_params(params, event_id, sensor_id):
    validate_event_params(params)
    event_header = EventSetupHeader(event_type=EVENT_TYPES[params['event_type']],
                                    event_id=event_id,
                                    sensor_id=sensor_id,
                                    suppress_time=params['suppress_time'],
                                    polarity=params['polarity'],
                                    sampling_window_size=params['sampling_window_size'],
                                    report_topic_len=len(params['topic']))
    return event_header


class Event(ABC):

    def __init__(self, event_id, sensor_id, callback, cbdata):
        """
        initialize Event object with event params, callback function and callback
        data variable that will be supplied when the callback function invocation
        happens.

         NOTE: current event implementation in C++ limits sensor value types to
               those that can be casted to uint16_t. This cast is done in the
               transit from sensor value to sampling_window.
        """
        self.event_id = event_id
        self.sensor_id = sensor_id
        self.callback = callback
        self.cbdata = cbdata

        # validate and assign event header tuple
        self.event_header = None
        self.topic = None

    def update_params(self, params):
        self.event_header = event_header_params(params, self.event_id, self.sensor_id)
        self.topic = params['topic']

    @abstractmethod
    def compile(self):
        """specific events parser. output is a transmittable binary msg"""
        pass

    def _compile_header(self):
        event_header_bytes = struct.pack('<HHHfBBB', *self.event_header) + \
                             self.topic.encode('ascii')
        return event_header_bytes

    @staticmethod
    def create_event(event_type, event_id, sensor_id, params, callback, cbdata):
        args = (event_id, sensor_id, params, callback, cbdata)
        if event_type == 'in_range':
            return EventInRange(*args)
        else:
            raise ValueError('invalid event type')


# Event: In Range
# Fires when sensor value is in range, qualified with statistical conditioning
IN_RANGE_HEADER_MEMBERS = ['min', 'max', 'confidence']
IN_RANGE_MANDATORY_KEYS = IN_RANGE_HEADER_MEMBERS

# distances are in cm for the HC-SR04 sensor
IN_RANGE_DISTANCES = [0, 400]
CONFIDENCE_RANGE = [0.0, 1.0]

InRangeSetupHeader = namedtuple('InRangeSetupHeader', IN_RANGE_HEADER_MEMBERS)


def validate_in_range_params(params):
    validate_all_exist(params.keys(), IN_RANGE_MANDATORY_KEYS)

    if not is_in_range(params['min'], IN_RANGE_DISTANCES) or \
            not is_in_range(params['max'], IN_RANGE_DISTANCES) or \
            params['min'] > params['max']:
        raise ValueError('invalid min/max values')

    if not is_in_range(params['confidence'], CONFIDENCE_RANGE):
        raise ValueError('invalid confidence value')


def in_range_header_params(params):
    validate_in_range_params(params)
    header = InRangeSetupHeader(min=params['min'],
                                max=params['max'],
                                confidence=params['confidence'])
    return header


class EventInRange(Event):

    def __init__(self, event_id, sensor_id, params, callback, cbdata):
        super().__init__(event_id, sensor_id, callback, cbdata)

        # validate and assign event header tuple
        self.header = None
        self.update_params(params)

    def update_params(self, params):
        super().update_params(params)
        self.header = in_range_header_params(params['params'])

    def compile(self):
        event_header_bytes = super()._compile_header()
        specific_header_bytes = struct.pack('<HHf', *self.header)
        return event_header_bytes + specific_header_bytes
