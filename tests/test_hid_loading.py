"""
hid.py has to find libhidapi on the devices this project targets. musl systems
(OpenWrt) have no glibc ldconfig, no gcc and no ld, which is everything
ctypes.util.find_library() shells out to on Linux - so it returns None there
and the module must fall back to asking the dynamic linker directly.
"""

import ctypes
import ctypes.util
import importlib
import sys

import pytest


@pytest.fixture
def reload_hid(monkeypatch):
    """Import zytempmqtt.hid for real, with the conftest stub set aside."""
    def _load():
        monkeypatch.delitem(sys.modules, 'zytempmqtt.hid', raising=False)
        return importlib.import_module('zytempmqtt.hid')
    yield _load
    # Put the stub back so later tests keep working
    sys.modules.pop('zytempmqtt.hid', None)


class FakePtr:
    """Stands in for a hid_device_info linked list of a given length."""

    def __init__(self, remaining):
        self.remaining = remaining

    def __bool__(self):
        return self.remaining > 0

    @property
    def contents(self):
        return type('info', (), {'next': FakePtr(self.remaining - 1)})


class FakeStub:
    """A settable stand-in for a C function; restype/argtypes just stick."""

    def __init__(self, result=None):
        self.result = result
        self.restype = None
        self.argtypes = []

    def __call__(self, *a, **k):
        return self.result() if callable(self.result) else self.result


class FakeLib:
    """Enough of the hidapi surface for import-time setup to succeed."""

    def __init__(self, name, devices=0):
        self.name = name
        self.devices = devices
        self._funcs = {}

    def __getattr__(self, item):
        if item.startswith('_'):
            raise AttributeError(item)
        if item not in self._funcs:
            if item == 'hid_enumerate':
                self._funcs[item] = FakeStub(lambda: FakePtr(self.devices))
            else:
                self._funcs[item] = FakeStub(0)
        return self._funcs[item]


def test_loads_without_find_library(monkeypatch, reload_hid):
    """The musl case: find_library finds nothing, dlopen still can."""
    monkeypatch.setattr(ctypes.util, 'find_library', lambda name: None)

    attempted = []

    def fake_cdll(name, *a, **k):
        attempted.append(name)
        if name == 'libhidapi-hidraw.so.0':
            return FakeLib(name)
        raise OSError(f'{name}: cannot open shared object file')

    monkeypatch.setattr(ctypes, 'CDLL', fake_cdll)

    hid = reload_hid()

    assert hid._lib is not None, (
        'hid.py gave up when find_library() returned None, which is exactly '
        'what happens on a musl router')
    assert attempted, 'no library was tried by soname'


def test_prefers_hidraw_when_both_see_devices(monkeypatch, reload_hid):
    """hidraw needs no kernel driver detach, so it wins the tie-break."""
    monkeypatch.setattr(ctypes.util, 'find_library', lambda name: None)
    monkeypatch.setattr(ctypes, 'CDLL',
                        lambda name, *a, **k: FakeLib(name, devices=2))

    hid = reload_hid()

    assert 'hidraw' in hid._lib.name, (
        f'loaded {hid._lib.name}; hidraw should win when both work')


def test_skips_a_backend_that_sees_nothing(monkeypatch, reload_hid):
    """The failure seen on a Pi: the kernel had no /dev/hidraw* at all, so the
    hidraw build enumerated nothing while libusb found the sensor. Picking a
    backend without checking left the service reporting 'No device found'
    with the device plugged in."""
    monkeypatch.setattr(ctypes.util, 'find_library', lambda name: None)

    def fake_cdll(name, *a, **k):
        return FakeLib(name, devices=0 if 'hidraw' in name else 3)

    monkeypatch.setattr(ctypes, 'CDLL', fake_cdll)

    hid = reload_hid()

    assert 'libusb' in hid._lib.name, (
        f'loaded {hid._lib.name}, which cannot see any device; '
        f'the backend that can should have been chosen')


def test_falls_back_when_no_backend_sees_anything(monkeypatch, reload_hid):
    """No HID devices attached is not a reason to refuse to import."""
    monkeypatch.setattr(ctypes.util, 'find_library', lambda name: None)
    monkeypatch.setattr(ctypes, 'CDLL',
                        lambda name, *a, **k: FakeLib(name, devices=0))

    hid = reload_hid()

    assert hid._lib is not None


def test_error_names_the_packages_to_install(monkeypatch, reload_hid):
    monkeypatch.setattr(ctypes.util, 'find_library', lambda name: None)

    def fake_cdll(name, *a, **k):
        raise OSError(f'{name}: cannot open shared object file')

    monkeypatch.setattr(ctypes, 'CDLL', fake_cdll)

    with pytest.raises(ImportError) as excinfo:
        reload_hid()
    assert 'hidapi' in str(excinfo.value).lower()
