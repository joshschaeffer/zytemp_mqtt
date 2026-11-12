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
        self.calls = 0
        self.restype = None
        self.argtypes = []

    def __call__(self, *a, **k):
        self.calls += 1
        return self.result() if callable(self.result) else self.result


class FakeLib:
    """Enough of the hidapi surface for import-time setup to succeed."""

    def __init__(self, name, devices=0):
        self._name = name          # ctypes.CDLL exposes the same attribute
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
        if name == 'libhidapi-libusb.so.0':
            return FakeLib(name)
        raise OSError(f'{name}: cannot open shared object file')

    monkeypatch.setattr(ctypes, 'CDLL', fake_cdll)

    hid = reload_hid()

    assert hid._lib is not None, (
        'hid.py gave up when find_library() returned None, which is exactly '
        'what happens on a musl router')
    assert attempted, 'no library was tried by soname'


def test_uses_find_library_when_sonames_fail(monkeypatch, reload_hid):
    """An install outside the linker's default path can still be found."""
    monkeypatch.setattr(ctypes.util, 'find_library',
                        lambda name: '/opt/lib/libhidapi-libusb.so.0'
                        if name == 'hidapi-libusb' else None)

    def fake_cdll(name, *a, **k):
        if name.startswith('/opt/lib/'):
            return FakeLib(name)
        raise OSError(f'{name}: cannot open shared object file')

    monkeypatch.setattr(ctypes, 'CDLL', fake_cdll)

    hid = reload_hid()

    assert hid._lib.name == '/opt/lib/libhidapi-libusb.so.0'


def test_never_loads_hidraw(monkeypatch, reload_hid):
    """The hidraw build lists devices from /sys and opens them from /dev.

    In a container those disagree - /sys is the host's, /dev is the
    container's - so hidraw lists a sensor it can never open, and when it
    was preferred it won over a libusb build that worked. Having it
    installed must make no difference.
    """
    attempted = []

    def fake_cdll(name, *a, **k):
        attempted.append(name)
        return FakeLib(name)

    monkeypatch.setattr(ctypes.util, 'find_library',
                        lambda name: f'lib{name}.so.0')
    monkeypatch.setattr(ctypes, 'CDLL', fake_cdll)

    hid = reload_hid()

    assert 'libusb' in hid._lib.name
    assert not [n for n in attempted if 'hidraw' in n], (
        f'tried to load a hidraw build: {attempted}')


def test_hidraw_alone_is_not_enough(monkeypatch, reload_hid):
    """Only the libusb build is supported; finding hidraw alone is an error,
    not a silent fallback to a backend that may list but not open."""
    monkeypatch.setattr(ctypes.util, 'find_library', lambda name: None)

    def fake_cdll(name, *a, **k):
        if 'hidraw' in name:
            return FakeLib(name)
        raise OSError(f'{name}: cannot open shared object file')

    monkeypatch.setattr(ctypes, 'CDLL', fake_cdll)

    with pytest.raises(ImportError):
        reload_hid()


def test_skips_a_library_that_is_not_hidapi(monkeypatch, reload_hid):
    """A file by the right name that lacks the hidapi symbols is passed over."""
    monkeypatch.setattr(ctypes.util, 'find_library', lambda name: None)

    class NotHidapi:
        _name = 'libhidapi-libusb.so.0'

        def __getattr__(self, item):
            raise AttributeError(item)

    def fake_cdll(name, *a, **k):
        if name == 'libhidapi-libusb.so.0':
            return NotHidapi()
        return FakeLib(name)

    monkeypatch.setattr(ctypes, 'CDLL', fake_cdll)

    hid = reload_hid()

    assert hid._lib.name == 'libhidapi-libusb.so'


def test_backend_name_reports_the_library_in_use(monkeypatch, reload_hid):
    """Reported when no device is found, so it has to name something real."""
    monkeypatch.setattr(ctypes.util, 'find_library', lambda name: None)
    monkeypatch.setattr(ctypes, 'CDLL', lambda name, *a, **k: FakeLib(name))

    hid = reload_hid()

    assert hid.backend_name() == 'libhidapi-libusb.so.0'


def test_error_names_the_packages_to_install(monkeypatch, reload_hid):
    monkeypatch.setattr(ctypes.util, 'find_library', lambda name: None)

    def fake_cdll(name, *a, **k):
        raise OSError(f'{name}: cannot open shared object file')

    monkeypatch.setattr(ctypes, 'CDLL', fake_cdll)

    with pytest.raises(ImportError) as excinfo:
        reload_hid()
    assert 'libhidapi-libusb0' in str(excinfo.value)
