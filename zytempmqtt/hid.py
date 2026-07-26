"""
ctypes wrapper around the hidapi C library, providing the same interface
as the python-hidapi (hid) module so the rest of the code is unchanged.
"""

import ctypes
import ctypes.util

_lib = None
for _name in ('hidapi-libusb', 'hidapi-hidraw', 'hidapi'):
    _libname = ctypes.util.find_library(_name)
    if _libname:
        try:
            _lib = ctypes.CDLL(_libname)
            break
        except OSError:
            continue

if _lib is None:
    raise ImportError(
        'Could not find hidapi shared library - install libhidapi-hidraw0 '
        'or libhidapi-libusb0 (package name varies by distro)')

_lib.hid_init()


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

_lib.hid_enumerate.restype = ctypes.POINTER(_DeviceInfo)
_lib.hid_enumerate.argtypes = [ctypes.c_ushort, ctypes.c_ushort]
_lib.hid_free_enumeration.restype = None
_lib.hid_free_enumeration.argtypes = [ctypes.POINTER(_DeviceInfo)]
_lib.hid_open_path.restype = ctypes.c_void_p
_lib.hid_open_path.argtypes = [ctypes.c_char_p]
_lib.hid_send_feature_report.restype = ctypes.c_int
_lib.hid_send_feature_report.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
_lib.hid_read.restype = ctypes.c_int
_lib.hid_read.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
_lib.hid_close.restype = None
_lib.hid_close.argtypes = [ctypes.c_void_p]


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
