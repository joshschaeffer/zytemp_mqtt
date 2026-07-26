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

# hidapi comes in two flavours. hidraw talks to /dev/hidraw*, libusb talks to
# the USB device directly. Which one works is a property of the machine, not
# something worth guessing at: a kernel built without hidraw, or one where
# nothing binds the device to usbhid, has no /dev/hidraw* at all and the
# hidraw build then enumerates nothing while libusb is perfectly happy.
# hidraw is listed first only as a tie-break, because it needs no kernel
# driver detach when both can see the device.
_BACKENDS = ('hidapi-hidraw', 'hidapi-libusb', 'hidapi')

# Asking the dynamic linker directly is what works everywhere. On Linux
# ctypes.util.find_library() shells out to ldconfig, gcc or ld to resolve a
# name, and a router has none of those - musl does not even ship a compatible
# ldconfig - so it returns None there and we would never find an installed
# library. dlopen() needs no external tooling, so try sonames first and treat
# find_library() as a bonus for unusual install locations.
_SONAMES = tuple(
    f'lib{_name}.so{_suffix}'
    for _suffix in ('.0', '')
    for _name in _BACKENDS
)


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
    lib.hid_close.restype = None
    lib.hid_close.argtypes = [ctypes.c_void_p]
    lib.hid_exit.restype = ctypes.c_int
    lib.hid_exit.argtypes = []
    lib.hid_init()
    return lib


def _candidate_libraries():
    """Every hidapi build we can load, most preferred first."""
    seen = set()

    def _try(name):
        try:
            lib = ctypes.CDLL(name)
        except OSError:
            return None
        key = getattr(lib, '_name', name)
        if key in seen:
            return None
        seen.add(key)
        return lib

    for soname in _SONAMES:
        lib = _try(soname)
        if lib is not None:
            yield lib

    for name in _BACKENDS:
        found = ctypes.util.find_library(name)
        if found:
            lib = _try(found)
            if lib is not None:
                yield lib


def _sees_devices(lib):
    devs = lib.hid_enumerate(0, 0)
    found = bool(devs)
    if found:
        lib.hid_free_enumeration(devs)
    return found


def _load_library():
    """Pick a backend that can actually see the hardware.

    Falls back to the first one that loaded, so that a machine with no HID
    devices attached still gets a usable module rather than an ImportError.

    Only the chosen backend is left running. Probing means initialising each
    candidate in turn, and the builds are separate copies of the same library
    sitting on the same libusb and udev underneath - leaving two of them live
    in one process is asking for trouble, particularly around device removal.
    """
    chosen = None
    rejected = []

    for lib in _candidate_libraries():
        try:
            _bind(lib)
        except AttributeError:
            continue            # not a hidapi library after all
        if _sees_devices(lib):
            chosen = lib
            break
        rejected.append(lib)

    if chosen is None and rejected:
        # Nothing can see a device - no sensor attached, most likely. Keep the
        # most preferred one so the module still works when it turns up.
        chosen = rejected.pop(0)

    for lib in rejected:
        try:
            lib.hid_exit()
        except OSError:
            pass

    return chosen


_lib = _load_library()

if _lib is None:
    raise ImportError(
        'Could not find hidapi shared library - install libhidapi-hidraw0 '
        'or libhidapi-libusb0 (package name varies by distro)')


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
        return _lib.hid_send_feature_report(self._dev, buf, len(buf))

    def read(self, length):
        buf = ctypes.create_string_buffer(length)
        n = _lib.hid_read(self._dev, buf, length)
        if n < 0:
            raise OSError('HID read error')
        return list(buf.raw[:n])

    def close(self):
        if self._dev:
            _lib.hid_close(self._dev)
            self._dev = None
