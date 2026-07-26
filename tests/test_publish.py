"""The payload contract Home Assistant consumes, and the return value that
ZyTemp.discovery() relies on to decide whether to retry."""

import json

from zytempmqtt.mqtt import MqttClient
from zytempmqtt.ZyTemp import ZyTemp

from mini_broker import MiniBroker
from conftest import wait_until


def test_publish_rounds_floats_and_sets_retain(cfg):
    broker = MiniBroker().start()
    cfg.mqtt_port = broker.port
    client = MqttClient()
    client.connect()

    try:
        assert wait_until(lambda: client.connect_count == 1)
        assert client.publish(
            'zytemp-mqtt',
            {'CO2': 800, 'Temperature': 21.123456789},
            retain=True)
        assert wait_until(lambda: broker.published)

        topic, payload, retain = broker.published[0]
        assert topic == 'zytemp-mqtt'
        assert retain is True
        assert json.loads(payload) == {'CO2': 800, 'Temperature': 21.12346}
    finally:
        client.disconnect()
        broker.stop()


def test_publish_reports_failure_while_disconnected(cfg):
    client = MqttClient()
    assert client.publish('zytemp-mqtt', {'CO2': 800}) is False


def test_readings_are_not_retained(cfg, fake_hid):
    """A reading means 'as of now'.

    Retaining it would hand the last value to any new subscriber as the
    current one, so a stopped service or an unplugged sensor would present
    a confident, wrong measurement instead of nothing.
    """
    broker = MiniBroker().start()
    cfg.mqtt_port = broker.port
    client = MqttClient()
    client.connect()
    zt = ZyTemp(fake_hid, client)

    try:
        assert wait_until(lambda: client.connect_count == 1)
        zt.update('CO2', 800)
        zt.update('Temperature', 21.5)
        assert wait_until(lambda: broker.topics(cfg.mqtt_topic))

        for topic, _, retain in broker.published:
            if topic == cfg.mqtt_topic:
                assert retain is False, 'readings must not be retained'
    finally:
        client.disconnect()
        broker.stop()


def test_state_is_republished_after_a_reconnect(cfg, fake_hid):
    """update() only publishes on change, so a steady reading would otherwise
    leave the entity empty until it happened to move."""
    broker = MiniBroker().start()
    cfg.mqtt_port = broker.port
    client = MqttClient()
    client.connect()
    zt = ZyTemp(fake_hid, client)

    try:
        assert wait_until(lambda: client.connect_count == 1)
        zt.update('CO2', 800)
        zt.update('Temperature', 21.5)
        assert wait_until(lambda: broker.topics('zytemp-mqtt'))

        port = broker.port
        broker.stop()
        restarted = MiniBroker(port).start()
        try:
            assert wait_until(lambda: client.connect_count == 2, timeout=15.0)
            assert wait_until(lambda: (zt.discovery(),
                                       restarted.topics('zytemp-mqtt'))[1],
                              timeout=10.0), (
                'state was not re-published after the reconnect')
        finally:
            restarted.stop()
    finally:
        client.disconnect()
        broker.stop()
