import struct

from abc import ABC, abstractmethod
from collections import namedtuple
from lib.utils import get_hex_str, is_in_range, validate_all_exist

import logging

# TODO: feels like too much boiler plate code here. Find better way to represent
#       the sensors, probably not by mirroring the C++ class structures.
#       Analyze the repetitiveness in the code. The interface of the base class
#       should probably be retained: update, compile.

SENSOR_TYPES = {
    'distance': 0,
    'button':   1,
    'freeheap': 2,
}

# sensor sampling interval range. values are in milliseconds.
SAMPLE_INTERVAL_RANGE_MS = [10, 10000]

# publish_raw number is the frequency of publishing sensor output in a message
# used mainly for debug/calibration of triggers.
# value of 0 - disabled. 1 - publish every sample. 2 - every 2nd sample, etc...
PUBLISH_RAW_RANGE = [0, 1000]

# data pin range to check for. These match D0, D1, ... etc GPIO pins on the device.
DPIN_RANGE = [0, 10]

# sensor header members that will be parsed to binary members
# note: order must match the c++ struct order.
SENSOR_HEADER_MEMBERS = ['sensor_type', 'sensor_id', 'sample_interval_ms', 'publish_raw']

# mandatory keys that need to appear in params
SENSOR_MANDATORY_KEYS = list(set(SENSOR_HEADER_MEMBERS) - {'sensor_id'})

SensorSetupHeader = namedtuple('SensorSetupHeader', SENSOR_HEADER_MEMBERS)


def validate_sensor_params(params):
    validate_all_exist(params.keys(), SENSOR_MANDATORY_KEYS)

    if params['type'] not in SENSOR_TYPES.keys():
        raise ValueError('invalid sensor type')

    if not is_in_range(params['sample_interval_ms'], SAMPLE_INTERVAL_RANGE_MS):
        raise ValueError('sample_interval_ms out of bounds')

    if not is_in_range(params['publish_raw'], PUBLISH_RAW_RANGE):
        raise ValueError('publish_raw out of bounds')


def sensor_header_params(params, sensor_id):
    validate_sensor_params(params)
    sensor_header = SensorSetupHeader(sensor_type=SENSOR_TYPES[params['type']],
                                      sensor_id=sensor_id,
                                      sample_interval_ms=params['sample_interval_ms'],
                                      publish_raw=params['publish_raw'])
    return sensor_header


class Sensor(ABC):

    def __init__(self, sensor_id):
        self.sensor_id = sensor_id
        self.sensor_header = None

    def update_params(self, params):
        self.sensor_header = sensor_header_params(params, self.sensor_id)

    @abstractmethod
    def compile(self):
        """specific sensor parser. output is a transmittable binary msg"""
        pass

    def _compile_header(self):
        sensor_header_bytes = struct.pack('<HHHH', *self.sensor_header)
        return sensor_header_bytes

    @staticmethod
    def create_event(sensor_type, sensor_id, params):
        args = (sensor_id, params)
        if sensor_type == 'distance':
            return SensorDistance(*args)
        if sensor_type == 'button':
            return SensorButton(*args)
        if sensor_type == 'freeheap':
            return SensorFreeHeap(*args)
        raise ValueError('invalid event type')


# sensor: distance - HC-SR04P
DISTANCE_HEADER_MEMBERS = ['trig_dpin', 'echo_dpin']
DISTANCE_MANDATORY_KEYS = DISTANCE_HEADER_MEMBERS
DistanceSetupHeader = namedtuple('DistanceSetupHeader', DISTANCE_HEADER_MEMBERS)


def validate_distance_params(params):
    validate_all_exist(params.keys(), DISTANCE_MANDATORY_KEYS)

    if not is_in_range(params['trig_dpin'], DPIN_RANGE):
        raise ValueError('trig_dpin oob')

    if not is_in_range(params['echo_dpin'], DPIN_RANGE):
        raise ValueError('echo_dpin oob')


def distance_header_params(params):
    validate_distance_params(params)
    header = DistanceSetupHeader(trig_dpin=params['trig_dpin'],
                                 echo_dpin=params['echo_dpin'])
    return header


class SensorDistance(Sensor):

    def __init__(self, sensor_id, params):
        super().__init__(sensor_id)

        # validate and assign event header tuple
        self.header = None
        self.update_params(params)

    def update_params(self, params):
        super().update_params(params)
        self.header = distance_header_params(params['params'])

    def compile(self):
        sensor_header_bytes = super()._compile_header()
        specific_header_bytes = struct.pack('<BB', *self.header)
        return sensor_header_bytes + specific_header_bytes


BUTTON_HEADER_MEMBERS = ['button_dpin']
BUTTON_MANDATORY_KEYS = BUTTON_HEADER_MEMBERS
ButtonSetupHeader = namedtuple('ButtonSetupHeader', BUTTON_HEADER_MEMBERS)


def validate_button_params(params):
    validate_all_exist(params.keys(), BUTTON_MANDATORY_KEYS)

    if not is_in_range(params['button_dpin'], DPIN_RANGE):
        raise ValueError('button_dpin oob')


def button_header_params(params):
    validate_button_params(params)
    header = ButtonSetupHeader(button_dpin=params['button_dpin'])
    return header


class SensorButton(Sensor):
    def __init__(self, sensor_id, params):
        super().__init__(sensor_id)

        # validate and assign event header tuple
        self.header = None
        self.update_params(params)

    def update_params(self, params):
        super().update_params(params)
        self.header = distance_header_params(params['params'])

    def compile(self):
        sensor_header_bytes = super()._compile_header()
        specific_header_bytes = struct.pack('<BB', *self.header)
        return sensor_header_bytes + specific_header_bytes


'''
class SensorOld:
    def __init__(self, device_id, mclient):
        self.device_id = device_id
        self.mclient = mclient
        self.sensor_id = 0

    def configure(self, cfg_sensor_list):
        cfg_msg_list = []
        for sensor_cfg in cfg_sensor_list:
            if 'sensor' in sensor_cfg.keys():
                cfg_msg = parse_sensor_config(sensor_cfg['sensor'], self.sensor_id)
                self.sensor_id += 1
                cfg_msg_list.append(cfg_msg)
                print(get_hex_str(cfg_msg))

                topic = f'elements/{self.device_id}/operate/sensors/add'
                self.mclient.publish(topic, cfg_msg, 1, retain=False)
'''