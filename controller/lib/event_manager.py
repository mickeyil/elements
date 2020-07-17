from lib.event import Event

class EventManager:
    def __init__(self, mclient):
        self.mclient = mclient
        self.event_id = 0

        # list of events. event_id corresponds to index of event in the list
        self.events = []

        # tiggers identified by trigger_label
        self.triggers = {}

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
        registers new event coupled to a sensor. a callback function will be
        called when the event triggers. an optinal callback data that will be
        passed as an argument.
        """
        trigger_params = self.triggers[trigger_label]

        # search for existing ev
        event = None
        for idx in range(len(self.events)):
            if self.events[idx].sensor == sensor:
                event = self.events[idx]

        if event is None:
            event = Event.create_event(event_type, self.event_id, sensor.sensor_id, trigger_params, callback, cbdata)
            self.events.append(event)
            self.event_id += 1
        else:
            event.update_params(trigger_params)

        # send configuration to device
        event.send_configuration(mclient)
