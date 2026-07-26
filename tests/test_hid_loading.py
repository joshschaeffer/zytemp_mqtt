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


class FakeLib:
    """Enough of the hidapi surface for import-time setup to succeed."""

    def __init__(self, name):
        self.name = name

    def __getattr__(self, item):
        func = ctypes.CFUNCTYPE(ctypes.c_int)(lambda: 0)
        func.restype = None
        func.argtypes = []
        return func


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


def test_prefers_hidraw_over_libusb(monkeypatch, reload_hid):
    """Both backends present: hidraw is the better default on Linux and is
    what the shipped udev rule grants access to."""
    monkeypatch.setattr(ctypes.util, 'find_library', lambda name: None)

    attempted = []

    def fake_cdll(name, *a, **k):
        attempted.append(name)
        return FakeLib(name)          # everything loads

    monkeypatch.setattr(ctypes, 'CDLL', fake_cdll)

    hid = reload_hid()

    assert 'hidraw' in hid._lib.name, (
        f'loaded {hid._lib.name}; hidraw should be preferred')


def test_error_names_the_packages_to_install(monkeypatch, reload_hid):
    monkeypatch.setattr(ctypes.util, 'find_library', lambda name: None)

    def fake_cdll(name, *a, **k):
        raise OSError(f'{name}: cannot open shared object file')

    monkeypatch.setattr(ctypes, 'CDLL', fake_cdll)

    with pytest.raises(ImportError) as excinfo:
        reload_hid()
    assert 'hidapi' in str(excinfo.value).lower()
