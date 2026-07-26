"""
The bug this guards against needs two events, which is why it looked random.

First the broker restarts, dropping every retained message unless it persists
them - the Home Assistant discovery config with it. Nothing appears wrong: the
client reconnects and readings carry on. Then, some time later, Home Assistant
restarts, rebuilds its entities from retained discovery, finds none, and the
sensors are unknown until the publisher itself is restarted.

Announcing once per process is what makes that permanent, so the fix is to
announce again on every new connection.
"""

from zytempmqtt.mqtt import MqttClient
from zytempmqtt.ZyTemp import ZyTemp

from mini_broker import MiniBroker
from conftest import wait_until


def test_discovery_is_republished_after_the_broker_restarts(cfg, fake_hid):
    broker = MiniBroker().start()
    cfg.mqtt_port = broker.port

    client = MqttClient()
    client.connect()
    zt = ZyTemp(fake_hid, client)

    # One config message per measurement. Wait for all of them rather than the
    # first: they are separate publishes, so a check for "any" can run while
    # the rest are still in flight.
    expected = len(ZyTemp.MEASUREMENTS)

    try:
        assert wait_until(lambda: client.connect_count == 1)
        assert wait_until(
            lambda: (zt.discovery(),
                     len(broker.topics('config')) >= expected)[1], timeout=5.0), (
            'the entities were never announced on the first connection')
        first = sorted(broker.topics('config'))

        port = broker.port
        broker.stop()

        restarted = MiniBroker(port).start()
        try:
            assert wait_until(lambda: client.connect_count == 2, timeout=15.0), (
                'client never reconnected after the broker came back')
            assert wait_until(
                lambda: (zt.discovery(),
                         len(restarted.topics('config')) >= expected)[1],
                timeout=10.0), (
                'discovery was not re-announced after the restart, so Home '
                'Assistant has no config for the entities')
            assert sorted(restarted.topics('config')) == first
        finally:
            restarted.stop()
    finally:
        client.disconnect()
        broker.stop()


def test_discovery_config_is_retained(cfg, fake_hid):
    """HA relies on the retain flag to pick the config up when it starts."""
    broker = MiniBroker().start()
    cfg.mqtt_port = broker.port
    client = MqttClient()
    client.connect()
    zt = ZyTemp(fake_hid, client)

    expected = len(ZyTemp.MEASUREMENTS)

    try:
        assert wait_until(lambda: client.connect_count == 1)
        assert wait_until(
            lambda: (zt.discovery(),
                     len(broker.topics('config')) >= expected)[1], timeout=5.0)
        for topic, _, retain in broker.published:
            if topic.endswith('config'):
                assert retain is True, f'{topic} was published without retain'
    finally:
        client.disconnect()
        broker.stop()


class _ReconnectingDuringPublish:
    """An MqttClient whose connection is replaced midway through publishing.

    Models a reconnect landing between discovery()'s guard and the point where
    it records which connection it announced on.
    """

    def __init__(self):
        self.connect_count = 1
        self.published = []
        self._bumped = False

    def is_connected(self):
        return True

    def publish(self, topic, pkt, retain=False):
        if not self._bumped:
            self._bumped = True
            self.connect_count += 1        # a reconnect, mid-discovery
        self.published.append(topic)
        return True


def test_a_reconnect_during_discovery_is_not_marked_as_announced(cfg, fake_hid):
    """Guards the check-then-act window in discovery().

    Without a snapshot, discovery() compares against one connection number and
    stores a newer one, so the new session is recorded as already announced and
    never gets its config - the original bug, reintroduced as a race.
    """
    fake = _ReconnectingDuringPublish()
    zt = ZyTemp(fake_hid, fake)

    zt.discovery()
    announced_on = zt.discovered_connection
    assert fake.published, 'nothing was published at all'

    # The connection the config actually landed on, not the one in flight
    assert announced_on == 1, (
        f'discovery recorded connection {announced_on} but published on 1; '
        f'connection 2 will never be announced')

    fake.published.clear()
    zt.discovery()
    assert fake.published, (
        'the new connection was never announced - discovery suppressed itself')
