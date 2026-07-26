"""What happens when the sensor is missing, or listed but unusable."""

import sys

import zytempmqtt.ZyTemp as zt_module
from zytempmqtt.ZyTemp import get_hiddev, CO2_USB_MFG, CO2_USB_PRD


def _sensor_entry():
    return {
        'manufacturer_string': CO2_USB_MFG,
        'product_string': CO2_USB_PRD,
        'interface_number': 0,
        'path': b'/dev/hidraw0',
        'vendor_id': 0x04d9,
        'product_id': 0xa052,
    }


def test_no_device_present(monkeypatch):
    monkeypatch.setattr(sys.modules['zytempmqtt.hid'], 'enumerate',
                        lambda *a, **k: [], raising=False)
    assert get_hiddev() is None


def test_other_hid_devices_are_ignored(monkeypatch):
    other = dict(_sensor_entry(), manufacturer_string='Someone else',
                 product_string='Something else')
    monkeypatch.setattr(sys.modules['zytempmqtt.hid'], 'enumerate',
                        lambda *a, **k: [other], raising=False)
    assert get_hiddev() is None


def test_device_that_cannot_be_opened_does_not_kill_the_service(monkeypatch):
    """Listed but not openable - unplugged mid-call, or permissions.

    The caller retries every few seconds, so this has to come back as "no
    device". Raising instead takes the process down, and systemd gives up
    altogether once the start limit is hit.
    """
    hid = sys.modules['zytempmqtt.hid']
    monkeypatch.setattr(hid, 'enumerate',
                        lambda *a, **k: [_sensor_entry()], raising=False)

    class Unopenable:
        def open_path(self, path):
            raise OSError('Failed to open HID device')

    monkeypatch.setattr(hid, 'device', Unopenable, raising=False)
    monkeypatch.setattr(hid, 'backend_name', lambda: 'test', raising=False)

    assert get_hiddev() is None       # must not raise


def test_sensor_is_started_with_a_report_id(cfg, fake_hid):
    """The prefixed form is the one the kernel's hidraw path accepts."""
    from zytempmqtt.ZyTemp import ZyTemp

    ZyTemp(fake_hid, _NullMqtt())

    assert fake_hid.feature_reports, 'the sensor was never asked to start'
    sent = fake_hid.feature_reports[0]
    assert sent[:1] == b'\x00', f'no report id prefix: {sent!r}'
    assert sent[1:] == b'\xc4\xc6\xc0\x92\x40\x23\xdc\x96'


def test_falls_back_to_the_bare_key(cfg):
    """libusb has always taken the unprefixed key; keep working there."""
    from zytempmqtt.ZyTemp import ZyTemp
    from conftest import FakeHid

    hiddev = FakeHid(reject_report_id_0=True)
    ZyTemp(hiddev, _NullMqtt())

    assert hiddev.feature_reports == [b'\xc4\xc6\xc0\x92\x40\x23\xdc\x96'], (
        'did not fall back when the prefixed form was refused')


class _NullMqtt:
    connect_count = 0

    def is_connected(self):
        return False

    def publish(self, *a, **k):
        return False


def test_device_is_opened_when_available(monkeypatch):
    hid = sys.modules['zytempmqtt.hid']
    monkeypatch.setattr(hid, 'enumerate',
                        lambda *a, **k: [_sensor_entry()], raising=False)

    opened = []

    class Openable:
        def open_path(self, path):
            opened.append(path)

    monkeypatch.setattr(hid, 'device', Openable, raising=False)
    monkeypatch.setattr(hid, 'backend_name', lambda: 'test', raising=False)

    dev = get_hiddev()
    assert dev is not None
    assert opened == [b'/dev/hidraw0']
