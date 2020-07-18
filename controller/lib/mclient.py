import random
import logging

from collections import namedtuple
import paho.mqtt.publish
import paho.mqtt.client as mqtt

from lib.utils import get_hex_str

Callback = namedtuple('Callback', ['name', 'function', 'cbdata'])


# The callback for when the client receives a CONNACK response from the server.
def on_connect(client, userdata, flags, rc):
    logging.info("Connected with result code " + str(rc))

    # Subscribing in on_connect() means that if we lose the connection and
    # reconnect then subscriptions will be renewed.

    # key value: topic : qos
    # example: { "topic1": 0, "topcic2" : 1 }
    topics = userdata['topics']
    for topic in topics.keys():
        logging.debug(f"subscribing to: [{topic}], qos={topics[topic]}")
        client.subscribe(topic, topics[topic])


# The callback for when a PUBLISH message is received from the server.
def on_message(client, userdata, msg):
    logging.debug(f"message arrived: topic: {msg.topic} content: {str(msg.payload)}")
    topic = msg.topic
    payload = msg.payload
    callbacks = userdata['callbacks']
    for c in callbacks:
        print(f"calling {c.name} callback.")
        c.callback(msg.topic, msg.payload, c.cbdata)


class MClient:
    def __init__(self, server_addr, client_id=None):
        self.server_addr = server_addr
        if not client_id:
            self.client_id = f"mclient-{random.randint(0, 1e8 - 1):08d}"
        else:
            self.client_id = client_id

        self.subscriptions = {"topics": {}, "callbacks": []}

        self.client = mqtt.Client(client_id=self.client_id, userdata=self.subscriptions)
        self.client.on_connect = on_connect
        self.client.on_message = on_message

        self.client.connect(server_addr, 1883, 60)

    def loop(self, timeout=0):
        if timeout == 0:
            self.client.loop_forever()
        else:
            self.client.loop(timeout=timeout)

    def publish(self, topic, payload, qos, retain=False):
        logging.debug(f"publish: topic: {topic} content: {get_hex_str(payload)}")
        self.client.publish(topic=topic, payload=payload, qos=qos, retain=retain)

    def publish_single(self, topic, payload, qos, retain):
        logging.debug(f"publish single: topic: {topic} -- content: {get_hex_str(payload)}")
        paho.mqtt.publish.single(topic, payload=payload, qos=qos, retain=retain,
                                 hostname=self.server_addr, client_id=self.client_id)

    def subscribe(self, topic, qos):
        self.client.subscribe(topic, qos)
        self.subscriptions['topics'][topic] = qos

    def register_callback(self, name, function, cbdata):
        callbacks = self.subscriptions['callbacks']
        for i in range(len(callbacks)):
            if callbacks[i].name == name:
                callbacks[i] = Callback(name=name, function=function, cbdata=cbdata)
                break
        else:
            callbacks.append(Callback(name=name, function=function, cbdata=cbdata))
