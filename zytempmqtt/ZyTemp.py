# https://hackaday.io/project/5301-reverse-engineering-a-low-cost-usb-co-monitor

import os
import logging as log
from . import hid
from .config import ConfigFile

CO2_USB_MFG = 'Holtek'
CO2_USB_PRD = 'USB-zyTemp'

# Ignore first 5 measurements during self-calibration after power-up
IGNORE_N_MEASUREMENTS = 5

# The sensor reports every couple of seconds, so silence for this long means
# something is wrong rather than merely quiet. Waiting rather than blocking
# forever is what makes that difference visible at all.
READ_TIMEOUT_MS = 5000
SILENT_READS_BEFORE_GIVING_UP = 6

l = log.getLogger('zytemp')


_CO2MON_MAGIC_WORD = b'Htemp99e'
_CO2MON_MAGIC_TABLE = (0, 0, 0, 0, 0, 0, 0, 0)


def list_to_longint(x):
    return sum([val << (i * 8) for i, val in enumerate(x[::-1])])


def longint_to_list(x):
    return [(x >> i) & 0xFF for i in (56, 48, 40, 32, 24, 16, 8, 0)]


class ZyTemp():
    MEASUREMENTS = {
        0x42: {
            'name': 'Temperature',
            'unit': '°C',
            'conversion': lambda x: x / 16 - 273.15,
            'ha_device_class': 'temperature',
            'ha_icon': 'mdi:thermometer',
        },
        0x50: {
            'name': 'CO2',
            'unit': 'ppm',
            'conversion': lambda x: x,
            'ha_device_class': 'carbon_dioxide',
            'ha_icon': 'mdi:molecule-co2',
        },
    }

    def __init__(self, hiddev, mqtt):
        self.cfg = ConfigFile()
        self.m = mqtt
        self.h = hiddev
        self.measurements_to_ignore = IGNORE_N_MEASUREMENTS
        self.values = {v['name']: None for v in ZyTemp.MEASUREMENTS.values()}
        self.discovered_connection = None

        self._magic_word = [((w << 4) & 0xFF) | (w >> 4)
                            for w in bytearray(_CO2MON_MAGIC_WORD)]
        self._magic_table = _CO2MON_MAGIC_TABLE
        self._magic_table_int = list_to_longint(_CO2MON_MAGIC_TABLE)

        self._start_sensor()

    def _start_sensor(self):
        """Ask the sensor to begin reporting.

        The first byte of a feature report is the report id. hidraw goes
        through the kernel, which checks it against the device's descriptor
        and refuses an id that was never declared; libusb bypasses that check
        entirely, which is why the bare key this project has always sent
        works there and goes nowhere over hidraw. Prefer the correct form and
        keep the old one as a fallback, because a sensor that is never asked
        just stays silent - the hardest failure to recognise.
        """
        key = bytes(self._magic_table if self.cfg.decrypt
                    else b'\xc4\xc6\xc0\x92\x40\x23\xdc\x96')

        for payload, form in ((b'\x00' + key, 'report id 0'), (key, 'bare')):
            try:
                self.h.send_feature_report(payload)
            except OSError:
                continue
            l.log(log.DEBUG, f'sensor started ({form})')
            return

        l.log(log.ERROR,
              f'Could not start the sensor via {hid.backend_name()} - '
              f'it will most likely report nothing')

    def __del__(self):
        self.h.close()

    """ MQTT Discovery for Home Assistant """

    def discovery(self):
        """Announce ourselves again whenever a new connection is established.

        A broker restart takes every retained message with it unless the
        broker persists them, the discovery config included. Announcing only
        once per process leaves no trace of the problem: the client
        reconnects, readings keep flowing, and everything looks healthy while
        the broker holds no config at all. The damage only surfaces the next
        time Home Assistant restarts and tries to rebuild its entities from
        retained discovery - at which point the sensors are gone for good,
        until this process happens to be restarted.
        """
        # Sample the counter once. It is incremented on paho's network thread,
        # so it can change while we are publishing; comparing and storing the
        # same value stops a reconnect that lands midway through from being
        # recorded as already announced.
        session = self.m.connect_count

        if self.discovered_connection == session:
            return

        # Nothing can be announced while offline, and this runs once per
        # reading - without this the journal fills with failure notices for
        # as long as the broker is away.
        if not self.m.is_connected():
            return

        if not self._publish_discovery_config():
            l.log(
                log.INFO, f'MQTT discovery to {self.cfg.mqtt_host} failed - retrying')
            return

        self.discovered_connection = session
        # update() only publishes on change, so without this the entities
        # would stay empty until a value happened to move.
        self.publish_state()

    def _publish_discovery_config(self):
        """Publish the Home Assistant discovery config.

        Returns whether the announcement succeeded; discovery being switched
        off counts as success, so the state below is still re-published.
        """
        if not len(self.cfg.discovery_prefix):
            return True

        res = []
        for meas in ZyTemp.MEASUREMENTS.values():
            id = os.path.basename(self.cfg.mqtt_topic)
            config_content = {
                'device': {
                    'identifiers': [id],
                    'manufacturer': CO2_USB_MFG,
                    'model': CO2_USB_PRD,
                    'name': self.cfg.friendly_name,
                },
                'enabled_by_default': True,
                'state_class': 'measurement',
                'device_class': meas['ha_device_class'],
                'name': ' '.join((self.cfg.friendly_name, meas['name'])),
                'state_topic': self.cfg.mqtt_topic,
                'unique_id': '_'.join((id, meas['name'])),
                'unit_of_measurement': meas['unit'],
                'value_template': '{{ value_json.%s }}' % meas['name'],
                'icon': meas['ha_icon']
            }
            res_val = self.m.publish(
                os.path.join(
                    self.cfg.discovery_prefix, 'sensor', config_content['unique_id'], 'config'
                ),
                config_content,
                retain=True
            )
            res.append(res_val)

        if not all(res):
            return False

        l.log(log.INFO, f'MQTT discovery published to {self.cfg.mqtt_host}')
        return True

    def publish_state(self):
        if any(v is None for v in self.values.values()):
            return

        # Deliberately not retained. A reading means "as of now", and a
        # retained one would be handed to any new subscriber as the current
        # value however old it was - so a stopped service or an unplugged
        # sensor would show up as a confident, wrong measurement. The
        # discovery config above is retained because it is static metadata;
        # this is not.
        self.m.publish(self.cfg.mqtt_topic, self.values)

    def update(self, key, value):
        if self.values[key] == value:
            return

        self.values[key] = value
        self.publish_state()

    def run(self):
        silent_reads = 0

        while True:
            self.discovery()
            try:
                r = self.h.read(8, timeout_ms=READ_TIMEOUT_MS)
            except OSError as err:
                # Close it here rather than leaving it to __del__. The handle
                # refers to hardware that has just gone away, and holding it
                # open until the object happens to be collected means calling
                # into the library about a device that no longer exists.
                l.log(log.ERROR, f'OS error: {err}')
                self.h.close()
                return

            if not r:
                silent_reads += 1
                l.log(log.WARNING,
                      f'No data from the sensor for '
                      f'{silent_reads * READ_TIMEOUT_MS // 1000}s')
                if silent_reads >= SILENT_READS_BEFORE_GIVING_UP:
                    # Reopening costs little and re-sends the report that asks
                    # the sensor to start, which is the likeliest thing to
                    # have gone wrong.
                    l.log(log.ERROR, 'Giving up on this handle and reopening')
                    self.h.close()
                    return
                continue

            silent_reads = 0

            if self.cfg.decrypt:
                # Rearrange message and convert to long int
                msg = list_to_longint([r[i] for i in [2, 4, 0, 7, 1, 6, 5, 3]])
                # XOR with magic_table
                res = msg ^ self._magic_table_int
                # Cyclic shift by 3 to the right
                res = (res >> 3) | ((res << 61) & 0xFFFFFFFFFFFFFFFF)
                # Convert to list
                res = longint_to_list(res)
                # Subtract and convert to uint8
                r = [(r - mw) & 0xFF for r, mw in zip(res, self._magic_word)]

            if r[4] != 0x0d:
                l.log(log.DEBUG, f'Unexpected data from device')
                continue

            if r[3] != sum(r[0:3]) & 0xff:
                l.log(log.ERROR, f'Checksum error')
                continue

            m_type = r[0]
            m_val = r[1] << 8 | r[2]

            try:
                m = ZyTemp.MEASUREMENTS[m_type]
            except KeyError:
                l.log(log.DEBUG, f'Unknown key {m_type:02x}')
                continue

            m_name, m_unit, m_reading = m['name'], m['unit'], m['conversion'](
                m_val)

            ignore = self.measurements_to_ignore > 0

            l.log(log.DEBUG, f'{m_name}: {m_reading:g} {m_unit}' +
                  (' (ignored)' if ignore else ''))

            if not ignore:
                self.update(m_name, m_reading)

            if m_name == 'CO2' and self.measurements_to_ignore:
                self.measurements_to_ignore -= 1


def get_hiddev():
    devices = hid.enumerate()
    hid_sensors = [
        e for e in devices
        if e['manufacturer_string'] == CO2_USB_MFG
        and e['product_string'] == CO2_USB_PRD
    ]

    p = []
    for s in hid_sensors:
        intf, path, vid, pid = (
            s[k] for k in
            ('interface_number', 'path', 'vendor_id', 'product_id')
        )
        path_str = path.decode('utf-8')
        l.log(log.INFO,
              f'Found CO2 sensor at intf. {intf}, {path_str}, VID={vid:04x}, PID={pid:04x}')
        p.append(path)

    if not p:
        # Name the backend: it is entirely possible for the sensor to be
        # plugged in and enumerating over USB while the hidapi build in use
        # cannot see it at all, and that is otherwise invisible from here.
        l.log(log.ERROR,
              f'No device found - {hid.backend_name()} lists '
              f'{len(devices)} HID device(s)')
        return None

    l.log(log.INFO, f'Using device at {p[0].decode("utf-8")} '
                    f'via {hid.backend_name()}')
    h = hid.device()
    try:
        h.open_path(p[0])
    except OSError as err:
        # Unplugged between listing and opening, or listed but not readable
        # by this user. Neither is worth dying for: the caller retries, and
        # letting it out of here kills the service instead - permanently,
        # once systemd's start limit is reached.
        l.log(log.ERROR, f'Cannot open device: {err}')
        return None
    return h
