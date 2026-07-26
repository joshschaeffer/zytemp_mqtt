import logging as log
import paho.mqtt.client as mqtt
import json

from .config import ConfigFile

l = log.getLogger('mqtt')

# Bounds for paho's exponential reconnect backoff. Its own default cap is 120s,
# which is a long time to be silent on a device that may well have booted before
# the broker did.
RECONNECT_MIN_DELAY = 1
RECONNECT_MAX_DELAY = 60

# How long a silent connection is allowed to look healthy. A link can die
# without either end noticing - no FIN, just nothing - and until a ping goes
# unanswered the socket appears perfectly fine while everything published into
# it is discarded.
KEEPALIVE = 30


class MqttClient:
    def __init__(self):
        self.cfg = ConfigFile()
        self.client = None
        # Incremented on every successful connect, so users of this client can
        # tell a fresh session apart from the previous one and re-publish
        # anything the broker may have lost (e.g. retained messages).
        # Written by paho's network thread, read by the main thread.
        self.connect_count = 0

    def _new_client(self):
        # paho-mqtt 2.x requires an explicit callback API version; 1.x has no
        # such argument. The callbacks below use the v1 signatures.
        if hasattr(mqtt, 'CallbackAPIVersion'):
            client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION1,
                client_id=self.cfg.mqtt_client_id)
        else:
            client = mqtt.Client(client_id=self.cfg.mqtt_client_id)
        client.on_connect = self.on_connect
        client.on_disconnect = self.on_disconnect
        client.on_connect_fail = self.on_connect_fail
        client.username_pw_set(
            self.cfg.mqtt_username, self.cfg.mqtt_password)
        client.reconnect_delay_set(
            min_delay=RECONNECT_MIN_DELAY, max_delay=RECONNECT_MAX_DELAY)
        return client

    # Both callbacks below run on paho's network thread, not the main one.

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connect_count += 1
            l.log(log.INFO, f'connected to {self.cfg.mqtt_host}')
        else:
            # Reaching the broker and being turned away is a different problem
            # from not reaching it at all - usually a wrong username or
            # password, which is worth saying rather than printing a number.
            l.log(log.ERROR,
                  f'{self.cfg.mqtt_host} refused the connection: '
                  f'{mqtt.connack_string(rc)}')

    def on_disconnect(self, client, userdata, rc):
        l.log(log.WARN, f'disconnected from {self.cfg.mqtt_host}: {rc}')

    def on_connect_fail(self, client, userdata):
        # Nothing else reports these: the connection is established on paho's
        # own thread, so a broker that cannot be resolved or reached would
        # otherwise just retry in silence forever.
        l.log(log.WARN,
              f'could not reach {self.cfg.mqtt_host}:{self.cfg.mqtt_port} '
              f'- retrying')

    def is_connected(self):
        return self.client is not None and self.client.is_connected()

    def connect(self):
        """Start connecting to the broker in the background.

        Returns immediately: the name lookup, the TCP connect and every
        subsequent retry happen on paho's network thread, so a broker that is
        down or unreachable never holds up the sensor read loop.
        """
        if self.client is not None:
            return True

        client = self._new_client()
        try:
            client.connect_async(self.cfg.mqtt_host, self.cfg.mqtt_port,
                                 keepalive=KEEPALIVE)
        except (ValueError, TypeError) as e:
            # An unusable host or port from the config file. There is nothing
            # to retry, and paho's network loop only handles OSError, so this
            # would otherwise kill the thread silently. Don't start it at all.
            l.log(log.ERROR, f'cannot connect to {self.cfg.mqtt_host}: {e}')
            return False

        self.client = client
        client.loop_start()
        l.log(log.INFO,
              f'connecting to {self.cfg.mqtt_host}:{self.cfg.mqtt_port}')
        return True

    def disconnect(self):
        client, self.client = self.client, None
        if client is None:
            return

        connected = client.is_connected()
        # Also takes the client out of the reconnect loop when it never got
        # connected in the first place, so the network thread stops retrying.
        client.disconnect()
        if connected:
            # Wait for the network thread so the DISCONNECT reaches the wire.
            # Only safe when we were connected: otherwise the thread may be
            # blocked in a name lookup or a TCP connect, and loop_stop() joins
            # without a timeout. It is a daemon thread that has already been
            # told to stop, so leaving it is harmless.
            client.loop_stop()

    def publish(self, topic, pkt, retain=False):
        def round_floats(o):
            if isinstance(o, float):
                return round(o, 5)
            if isinstance(o, dict):
                return {k: round_floats(v) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                return [round_floats(x) for x in o]
            return o
        if self.is_connected():
            mi = self.client.publish(topic, json.dumps(
                round_floats(pkt)), retain=retain)
            return (mi.rc == mqtt.MQTT_ERR_SUCCESS)
        else:
            return False
