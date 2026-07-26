"""
Connecting must never hold up the caller. The sensor read loop calls into the
MQTT client, so a broker that is down or unreachable would otherwise stop
readings entirely.
"""

import logging
import socket
import threading
import time

import pytest

import zytempmqtt.mqtt as zm
from zytempmqtt.mqtt import MqttClient

from mini_broker import (MiniBroker, RefusingBroker, RejectingBroker,
                         SilentBroker)
from conftest import wait_until

# Generous: the behaviour being guarded took seconds, so anything sub-second
# is unambiguous even on a loaded CI runner.
NON_BLOCKING = 0.25

# RFC 5737 TEST-NET-1. Reserved for documentation and not routed, so a
# connection attempt hangs rather than being refused.
BLACKHOLE_HOST = '192.0.2.1'


def free_port():
    """A port number with nothing listening on it."""
    s = socket.socket()
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_connect_does_not_block_when_refused(cfg):
    cfg.mqtt_port = free_port()
    client = MqttClient()

    t0 = time.time()
    client.connect()
    elapsed = time.time() - t0

    try:
        assert elapsed < NON_BLOCKING, (
            f'connect() blocked for {elapsed:.2f}s against a closed port; '
            f'this stalls the sensor read loop')
    finally:
        client.disconnect()


def test_connect_does_not_block_when_host_unreachable(cfg):
    cfg.mqtt_host = BLACKHOLE_HOST
    cfg.mqtt_port = 1883
    client = MqttClient()

    t0 = time.time()
    client.connect()
    elapsed = time.time() - t0

    try:
        assert elapsed < NON_BLOCKING, (
            f'connect() blocked for {elapsed:.2f}s against an unreachable '
            f'host; this stalls the sensor read loop')
    finally:
        client.disconnect()


def test_publish_returns_promptly_while_disconnected(cfg):
    cfg.mqtt_port = free_port()
    client = MqttClient()
    client.connect()

    try:
        t0 = time.time()
        ok = client.publish('zytemp-mqtt', {'CO2': 800})
        elapsed = time.time() - t0

        assert ok is False
        assert elapsed < NON_BLOCKING
    finally:
        client.disconnect()


def test_reconnects_itself_with_backoff(cfg, monkeypatch):
    """Retry without the caller driving it, but do not hammer.

    Also pins requirement that one Client is reused rather than a fresh one
    being built per attempt - that churn is what hurts on a small router.
    """
    broker = RefusingBroker().start()
    cfg.mqtt_port = broker.port

    created = []
    real_client = zm.mqtt.Client

    class CountingClient(real_client):
        def __init__(self, *a, **k):
            created.append(1)
            super().__init__(*a, **k)

    monkeypatch.setattr(zm.mqtt, 'Client', CountingClient)

    client = MqttClient()
    client.connect()
    try:
        # Nobody calls into the client during this window on purpose.
        assert wait_until(lambda: broker.accepts >= 2, timeout=8.0), (
            f'client did not retry on its own (accepts={broker.accepts}); '
            f'reconnect depends on the caller pumping it')
        assert broker.accepts <= 8, (
            f'{broker.accepts} connection attempts in 8s - no backoff')
        assert len(created) == 1, (
            f'{len(created)} Client objects built; one should be reused')
    finally:
        client.disconnect()
        broker.stop()


def test_rejected_credentials_read_differently_from_unreachable(cfg, caplog):
    """Being turned away by the broker is not the same problem as not
    finding it, and the log has to make that obvious."""
    broker = RejectingBroker().start()
    cfg.mqtt_port = broker.port
    client = MqttClient()

    try:
        with caplog.at_level(logging.WARNING, logger='mqtt'):
            client.connect()
            wait_until(lambda: any('refused' in r.getMessage()
                                   for r in caplog.records), timeout=10.0)

        messages = [r.getMessage() for r in caplog.records]
        assert any('refused' in m for m in messages), (
            f'no refusal reported, only: {messages}')
        # The reason, not a bare number
        assert any('password' in m.lower() or 'auth' in m.lower()
                   for m in messages), (
            f'refusal did not say why: {messages}')
        assert client.connect_count == 0, 'a refused connection is not a connection'
    finally:
        client.disconnect()
        broker.stop()


def test_unreachable_broker_says_so(cfg, caplog):
    cfg.mqtt_port = free_port()
    client = MqttClient()

    try:
        with caplog.at_level(logging.WARNING, logger='mqtt'):
            client.connect()
            wait_until(lambda: any('reach' in r.getMessage()
                                   for r in caplog.records), timeout=10.0)

        messages = [r.getMessage() for r in caplog.records]
        assert any('reach' in m for m in messages), (
            f'an unreachable broker went unreported: {messages}')
        assert not any('refused' in m for m in messages), (
            'an unreachable broker must not look like a rejected login')
    finally:
        client.disconnect()


def test_recovers_from_a_connection_that_dies_silently(cfg, monkeypatch):
    """The reported failure: the connection was lost and never came back.

    A link can die with no FIN and no reset - a vanished host, a reshuffled
    container network - leaving a socket that looks healthy while everything
    published into it is discarded. Only an unanswered keepalive reveals it,
    so anything that decides it is connected by remembering a past callback
    stays wrong forever. Nothing here closes a socket; that is the point.
    """
    monkeypatch.setattr(zm, 'KEEPALIVE', 2, raising=False)

    broker = SilentBroker().start()
    cfg.mqtt_port = broker.port
    client = MqttClient()
    client.connect()

    try:
        assert wait_until(lambda: client.connect_count == 1, timeout=10.0), (
            'never established the first connection')

        # The broker is now mute. Nothing signals this; the client has to
        # work it out from the keepalive going unanswered and start again.
        assert wait_until(lambda: client.connect_count >= 2, timeout=25.0), (
            'the dead connection was never noticed - the client believed it '
            'was still connected and would publish into a void indefinitely')

        assert broker.accepts >= 2, (
            'no reconnection was attempted')
    finally:
        client.disconnect()
        broker.stop()


def test_connection_survives_a_silent_sensor(cfg, monkeypatch):
    """MQTT liveness must not depend on the sensor producing readings.

    The client used to be serviced only after each HID read, and that read
    blocks with no timeout. An unplugged or wedged sensor therefore stopped
    the keepalive as well, and the connection died unnoticed with nothing
    left running to rebuild it. Nothing touches the client here for several
    keepalive periods, exactly as if no reading had arrived.
    """
    monkeypatch.setattr(zm, 'KEEPALIVE', 2, raising=False)

    broker = MiniBroker().start()
    cfg.mqtt_port = broker.port
    client = MqttClient()
    client.connect()

    try:
        assert wait_until(lambda: client.connect_count == 1, timeout=10.0)

        time.sleep(6)          # several keepalives, no calls into the client

        assert client.is_connected(), (
            'the connection lapsed while no readings were arriving')
        assert client.publish('zytemp-mqtt', {'CO2': 800}), (
            'publishing failed after a quiet period')
        assert client.connect_count == 1, (
            'the connection dropped and was rebuilt rather than being kept')
    finally:
        client.disconnect()
        broker.stop()


def test_disconnect_is_prompt_and_idempotent(cfg):
    """Must not join a network thread stuck in a name lookup or TCP connect."""
    cfg.mqtt_host = BLACKHOLE_HOST
    cfg.mqtt_port = 1883
    client = MqttClient()
    client.connect()

    t0 = time.time()
    client.disconnect()
    elapsed = time.time() - t0

    assert elapsed < 0.5, f'disconnect() took {elapsed:.2f}s'
    client.disconnect()          # second call must be harmless


def test_disconnect_stops_the_network_thread(cfg):
    from mini_broker import MiniBroker

    broker = MiniBroker().start()
    cfg.mqtt_port = broker.port
    client = MqttClient()
    client.connect()

    try:
        assert wait_until(lambda: client.connect_count == 1, timeout=10.0)
        before = threading.active_count()
        client.disconnect()
        assert wait_until(
            lambda: threading.active_count() < before or client.client is None,
            timeout=3.0)
    finally:
        broker.stop()
