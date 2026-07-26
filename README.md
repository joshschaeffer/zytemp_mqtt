# zytemp-mqtt

This is a MQTT interface for the Holtek USB-zyTemp chipset which is used in inexpensive CO2 monitors, such as [co2Meter.com](http://www.co2meter.com/products/co2mini-co2-indoor-air-quality-monitor) or [TFA Dostmann](https://www.amazon.de/dp/B00TH3OW4Q).

## Installation

Nothing here has to be compiled, which matters on the small devices this tends
to run on. Every dependency is already packaged by the distro, and the
application itself is plain Python that gets copied into place.

* Install the dependencies. `hidapi` is a C library, loaded at runtime via
  ctypes.

  ```bash
  sudo apt install python3-paho-mqtt python3-yaml libhidapi-hidraw0
  ```

  On OpenWrt: `opkg install python3-paho-mqtt python3-yaml hidapi kmod-usb-hid`

* Clone the repository and change into it - the install script works relative
  to its own directory.

  ```bash
  git clone https://github.com/patrislav1/zytemp_mqtt.git
  cd zytemp_mqtt
  ```

* Run the install script. It creates the service user and the udev rule, copies
  the application to `/opt/zytempmqtt`, writes a default config if there is not
  one already, and enables and starts the service.

  ```bash
  sudo ./install.sh
  ```

  It is safe to re-run to update: the code is refreshed, your configuration is
  left alone.

* Fill in the MQTT settings (see [Configuration](#configuration)) and restart.

  ```bash
  sudo nano /etc/zytempmqtt/config.yaml
  sudo systemctl restart zytempmqtt
  ```

* Check that it came up and is publishing.

  ```bash
  systemctl status zytempmqtt
  journalctl -u zytempmqtt -f
  ```

There is deliberately no `pip install` step. Recent distros refuse to let pip
write to the system Python at all, and a virtualenv or pipx would hide the
distro's own `python3-paho-mqtt` and `python3-yaml` - so it would fetch them
from PyPI and try to build `PyYAML` from source, which is the thing this avoids.
`setup.py` is still there if you would rather install it as a package on a
normal workstation.

## Uninstalling

```bash
sudo systemctl disable --now zytempmqtt
sudo rm -rf /opt/zytempmqtt /etc/zytempmqtt
sudo rm -f /lib/systemd/system/zytempmqtt.service
sudo rm -f /etc/udev/rules.d/90-usb-zytemp-permissions.rules
sudo systemctl daemon-reload
```

## Configuration

The configuration file is read from `$HOME/.config/zytempmqtt/config.yaml` if
that exists, and from `/etc/zytempmqtt/config.yaml` otherwise. The service runs
as the unprivileged `zytempmqtt` user, so the `/etc` path is the one that
applies to it - the `$HOME` path is only useful when running the module by hand.

It contains following configuration:

```yaml
mqtt_host: homeassistant.local  # MQTT server
mqtt_port: 1883                 # MQTT port (default: 1883)
mqtt_username: user             # MQTT username
mqtt_password: pass             # MQTT password
mqtt_client_id: foobar          # MQTT client ID (default: zytemp-mqtt)
mqtt_topic: /foo/bar            # MQTT topic (default: zytemp-mqtt)
friendly_name: aircontrol       # Friendly name for HomeAssistant (default: zytemp-mqtt)
discovery_prefix: homeassistant # Discovery prefix for HomeAssistant (default: homeassistant)
decrypt: False                  # Decrypt data from zyTemp, may be needed for some devices (default: False)
```

## Home Assistant integration

On startup, `zytemp-mqtt` performs [MQTT Discovery](https://www.home-assistant.io/docs/mqtt/discovery/) so that the sensors magically show up in the Home Assistant system without any manual configuration:

 ![HomeAssistant screenshot](mqtt.png)
