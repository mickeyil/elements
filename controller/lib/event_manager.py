import logging
import time
from lib.event import Event

# event handling time threshold, in seconds. above this value a warning will
# show in the logs
EVENT_HANDLING_TIME = 0.02


def event_callback(topic, payload, cbdata):
    topic_parts = topic.split('/')

    if len(topic_parts) < 5:
        return

    device_id = topic_parts[1]
    event_id = int(topic_parts[3])
    sensor_id = int(topic_parts[4])

    event_manager = cbdata
    event_manager.on_event(event_id, device_id, sensor_id, payload)


class EventManager:
    def __init__(self, mclient):
        self.mclient = mclient

        self.event_id = 0

        # list of events. event_id corresponds to index of event in the list
        self.events = []

        # triggers identified by trigger_label
        self.triggers = {}

        self.subscribed = False

    def add_trigger(self, params):
        """
        adds trigger condition for event from configuration. trigger is uniquely
        identified by trigger_label.
        """
        if 'trigger_label' not in params:
            raise ValueError('missing key: trigger_label')
        trigger_label = params['trigger_label']
        if 'trigger_label' in self.triggers:
            raise ValueError('trigger_label already exists')
        self.triggers[trigger_label] = params

    def add_event(self, trigger_label, sensor, callback, cbdata=None):
        """
        registers new event: trigger coupled to a sensor. a callback function
        will be called when the event triggers. an optinal callback data that
        will be passed as an argument.
        """
        if trigger_label not in self.triggers:
            raise RuntimeError('unknown event trigger')
        trigger_params = self.triggers[trigger_label]

        # search for existing event with the same sensor id. if such event exists
        # it's parameters will be updated.
        event = None
        for idx in range(len(self.events)):
            # same sensor instance means same device id and same sensor id
            if self.events[idx].sensor == sensor:
                event = self.events[idx]

        if event is None:
            event_type = trigger_params['event_type']
            event = Event.create_event(event_type, self.event_id, sensor, trigger_params, callback, cbdata)
            self.events.append(event)
            self.event_id += 1
        else:
            event.update_params(trigger_params)

        # send configuration to device
        event.send_configuration(self.mclient)

    def on_event(self, event_id, device_id, sensor_id, payload):
        t_s = time.time()
        # locate event in events list.
        # for a few sensors and a few events simple search should work fine.
        # in case of larger number of events an additional data structure would
        # probably be needed to reduce latency on this critical path.
        event = None
        for e in self.events:
            if e.event_id == event_id and \
                    e.sensor.device_id == device_id and \
                    e.sensor.sensor_id == sensor_id:
                event = e
                break

        if event is None:
            logging.warning(f"no event found for event_id: {event_id},"\
                    f"device_id: {device_id}, sensor_id: {sensor_id}")
            return

        callback = event.callback
        cbdata = event.cbdata

        # call callback function
        t_cb = time.time()
        callback(cbdata, payload)
        t_e = time.time()
        if t_e - t_s > EVENT_HANDLING_TIME:
            logging.warning(f"callback for event {event_id} on {device_id}:{sensor_id}"\
                            f"is above {EVENT_HANDLING_TIME}s. total: {t_e - t_s},"\
                            f"cb: {t_e - t_cb}")

    def subscribe_to_events(self):
        if not self.subscribed:
            self.mclient.subscribe(f"elements/+/events/#", 1)
            self.mclient.register_callback("event_callback", event_callback, self)
            self.subscribed = True
