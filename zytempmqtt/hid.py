"""
ctypes wrapper around the hidapi C library, providing the same interface
as the python-hidapi (hid) module so the rest of the code is unchanged.
"""

import ctypes
import ctypes.util

class _DeviceInfo(ctypes.Structure):
    pass


_DeviceInfo._fields_ = [
    ('path',                ctypes.c_char_p),
    ('vendor_id',           ctypes.c_ushort),
    ('product_id',          ctypes.c_ushort),
    ('serial_number',       ctypes.c_wchar_p),
    ('release_number',      ctypes.c_ushort),
    ('manufacturer_string', ctypes.c_wchar_p),
    ('product_string',      ctypes.c_wchar_p),
    ('usage_page',          ctypes.c_ushort),
    ('usage',               ctypes.c_ushort),
    ('interface_number',    ctypes.c_int),
    ('next',                ctypes.POINTER(_DeviceInfo)),
]

# Only the libusb build of hidapi is used. It talks to the USB device through
# /dev/bus/usb, which is there on every machine this has run on: a Pi, a
# RouterOS container and an Incus container. The hidraw build needs the kernel
# to have bound the sensor to usbhid and created /dev/hidraw*, and it lists
# devices from /sys while opening them from /dev - so in a container, which
# sees the host's /sys but only its own /dev, it lists a sensor it can never
# open. It also never worked where libusb did not. This is also what the
# project used originally: the hidapi pip package builds its `hid` module
# against libusb on Linux.
_BACKEND = 'hidapi-libusb'

# Asking the dynamic linker directly is what works everywhere. On Linux
# ctypes.util.find_library() shells out to ldconfig, gcc or ld to resolve a
# name, and a router has none of those - musl does not even ship a compatible
# ldconfig - so it returns None there and we would never find an installed
# library. dlopen() needs no external tooling, so try sonames first and treat
# find_library() as a bonus for unusual install locations.
_SONAMES = (f'lib{_BACKEND}.so.0', f'lib{_BACKEND}.so')


def _bind(lib):
    """Declare the signatures of the hidapi calls we make."""
    lib.hid_enumerate.restype = ctypes.POINTER(_DeviceInfo)
    lib.hid_enumerate.argtypes = [ctypes.c_ushort, ctypes.c_ushort]
    lib.hid_free_enumeration.restype = None
    lib.hid_free_enumeration.argtypes = [ctypes.POINTER(_DeviceInfo)]
    lib.hid_open_path.restype = ctypes.c_void_p
    lib.hid_open_path.argtypes = [ctypes.c_char_p]
    lib.hid_send_feature_report.restype = ctypes.c_int
    lib.hid_send_feature_report.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    lib.hid_read.restype = ctypes.c_int
    lib.hid_read.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
    lib.hid_read_timeout.restype = ctypes.c_int
    lib.hid_read_timeout.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int]
    lib.hid_close.restype = None
    lib.hid_close.argtypes = [ctypes.c_void_p]
    lib.hid_exit.restype = ctypes.c_int
    lib.hid_exit.argtypes = []
    lib.hid_init()
    return lib


def _candidate_names():
    """Names to hand to dlopen(), sonames first."""
    yield from _SONAMES
    found = ctypes.util.find_library(_BACKEND)
    if found:
        yield found


def _load_library():
    """Load and initialise the libusb build of hidapi, or return None."""
    for name in _candidate_names():
        try:
            lib = ctypes.CDLL(name)
        except OSError:
            continue
        try:
            return _bind(lib)
        except AttributeError:
            continue            # not a hidapi library after all
    return None


_lib = _load_library()

if _lib is None:
    raise ImportError(
        'Could not find the libusb build of hidapi (libhidapi-libusb.so) - '
        'install libhidapi-libusb0 on Debian/Ubuntu, hidapi on OpenWrt')


def backend_name():
    """Which hidapi build ended up being used.

    Worth reporting: when no device turns up, the backend in use is the
    first thing you want to know.
    """
    return getattr(_lib, '_name', None) or 'hidapi'


def enumerate(vendor_id=0, product_id=0):
    devs = _lib.hid_enumerate(vendor_id, product_id)
    result = []
    ptr = devs
    while ptr:
        d = ptr.contents
        result.append({
            'path':                d.path,
            'vendor_id':           d.vendor_id,
            'product_id':          d.product_id,
            'serial_number':       d.serial_number or '',
            'manufacturer_string': d.manufacturer_string or '',
            'product_string':      d.product_string or '',
            'usage_page':          d.usage_page,
            'usage':               d.usage,
            'interface_number':    d.interface_number,
        })
        ptr = d.next
    _lib.hid_free_enumeration(devs)
    return result


class device:
    def __init__(self):
        self._dev = None

    def open_path(self, path):
        self._dev = _lib.hid_open_path(path)
        if not self._dev:
            raise OSError('Failed to open HID device')

    def send_feature_report(self, data):
        buf = bytes(data)
        n = _lib.hid_send_feature_report(self._dev, buf, len(buf))
        if n < 0:
            # Worth raising rather than returning: this is what tells the
            # sensor to start reporting, and a device that was never told
            # simply goes quiet, which is indistinguishable from a healthy
            # one that has nothing to say.
            raise OSError('failed to send feature report')
        return n

    def read(self, length, timeout_ms=None):
        """Read a report. Returns an empty list if nothing arrived in time.

        Without a timeout this blocks indefinitely, which hides the
        difference between a sensor that is quiet and one that is broken.
        """
        buf = ctypes.create_string_buffer(length)
        if timeout_ms is None:
            n = _lib.hid_read(self._dev, buf, length)
        else:
            n = _lib.hid_read_timeout(self._dev, buf, length, timeout_ms)
        if n < 0:
            raise OSError('HID read error')
        return list(buf.raw[:n])

    def close(self):
        if self._dev:
            _lib.hid_close(self._dev)
            self._dev = None
