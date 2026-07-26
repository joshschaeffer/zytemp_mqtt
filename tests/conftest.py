import sys
import time
import types

import pytest

# zytempmqtt.hid loads libhidapi at import time and raises ImportError when it
# is missing, and ZyTemp imports it at module level. Stub it before anything
# imports the package, so the suite runs on a machine with no sensor and no
# hidapi installed. This must happen at import time: conftest is imported
# before any test module.
if 'zytempmqtt.hid' not in sys.modules:
    _stub = types.ModuleType('zytempmqtt.hid')
    _stub.enumerate = lambda *a, **k: []
    _stub.device = object
    _stub.backend_name = lambda: 'stub'
    sys.modules['zytempmqtt.hid'] = _stub

import zytempmqtt.mqtt as zm                   # noqa: E402
from zytempmqtt.config import ConfigFile      # noqa: E402


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch):
    """Retry quickly, so reconnect tests are quick and deterministic.

    Production backs off to a minute, which is right for a device that may
    outlive its broker but means a restart can land just after a failed
    attempt and leave a test waiting longer than its timeout.
    """
    monkeypatch.setattr(zm, 'RECONNECT_MIN_DELAY', 1, raising=False)
    monkeypatch.setattr(zm, 'RECONNECT_MAX_DELAY', 2, raising=False)


def wait_until(predicate, timeout=10.0, interval=0.02):
    """Poll until predicate() is true. Returns whether it became true.

    Preferred over fixed sleeps: fast when things work, slow only on failure.
    """
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@pytest.fixture
def cfg(monkeypatch):
    """A fresh ConfigFile. It is a singleton, so state leaks without this."""
    monkeypatch.setattr(ConfigFile, '_instance', None)
    c = ConfigFile()
    c.mqtt_host = '127.0.0.1'
    c.mqtt_port = 0
    c.mqtt_username = None
    c.mqtt_password = None
    c.mqtt_client_id = 'zytemp-mqtt-test'
    c.mqtt_topic = 'zytemp-mqtt'
    c.friendly_name = 'aircontrol'
    c.discovery_prefix = 'homeassistant'
    c.decrypt = False
    return c


class FakeHid:
    """Stands in for a hid device handle."""

    def __init__(self):
        self.feature_reports = []

    def send_feature_report(self, data):
        self.feature_reports.append(bytes(data))
        return len(data)

    def read(self, length):
        return []

    def close(self):
        pass


@pytest.fixture
def fake_hid():
    return FakeHid()
