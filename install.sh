#!/bin/sh

set -eu

SRVNAME="zytempmqtt"
INSTALL_DIR="/opt/${SRVNAME}"
CONFIG_DIR="/etc/${SRVNAME}"
CONFIG_FILE="${CONFIG_DIR}/config.yaml"

cd "$(dirname "$0")"

if [ ! -d "${SRVNAME}" ]; then
    echo "error: run this from a checkout - no ${SRVNAME}/ here" >&2
    exit 1
fi

# Add system user zytempmqtt

ID=$(id -u ${SRVNAME} 2>/dev/null || true)

if [ -f /usr/sbin/nologin ]; then
    USERSHELL="/usr/sbin/nologin"
elif [ -f /sbin/nologin ]; then
    USERSHELL="/sbin/nologin"
else
    USERSHELL="/bin/false"
fi

if [ ! $(getent group ${SRVNAME}) ]; then
    groupadd -f ${SRVNAME}
    echo "group: added ${SRVNAME}"
fi

if [ -z "$ID" ]; then
    useradd --system --shell $USERSHELL -g ${SRVNAME} ${SRVNAME}
    echo "user: added ${SRVNAME}"
fi

# Install udev rule

if grep -qa container=lxc /proc/1/environ; then
    echo "Skipping udev rules in lxc"
else
    cp -a udev/90-usb-zytemp-permissions.rules /etc/udev/rules.d/
    udevadm control --reload-rules
    udevadm trigger
fi

# Install the application. Copying it keeps the install free of pip, which
# recent distros refuse to run against the system Python anyway, and of the
# virtualenvs that would hide the distro's own paho-mqtt and PyYAML.

mkdir -p "${INSTALL_DIR}"
rm -rf "${INSTALL_DIR}/${SRVNAME}"
cp -a "${SRVNAME}" "${INSTALL_DIR}/"
echo "installed to ${INSTALL_DIR}"

# Create default config, but never overwrite one that is already there

mkdir -p "${CONFIG_DIR}"
if [ -e "${CONFIG_FILE}" ]; then
    echo "keeping existing ${CONFIG_FILE}"
else
    cat <<'EOF' > "${CONFIG_FILE}"
mqtt_host: homeassistant.local
mqtt_username: user
mqtt_password: pass
friendly_name: aircontrol-mini
EOF
    echo "wrote default ${CONFIG_FILE} - edit it before starting the service"
fi

# Add systemd service

SERVICE_PATH=/lib/systemd/system/${SRVNAME}.service

cat <<EOF > $SERVICE_PATH
[Unit]
Description="zytempmqtt service"
Documentation=https://github.com/patrislav1/zytemp_mqtt
After=network.target
StopWhenUnneeded=false
StartLimitIntervalSec=10
StartLimitInterval=10
StartLimitBurst=3
[Service]
Type=simple
User=${SRVNAME}
Group=${SRVNAME}
Restart=always
RestartSec=10
WorkingDirectory=${INSTALL_DIR}
Environment=PYTHONPATH=${INSTALL_DIR}
ExecStart=/usr/bin/python3 -m ${SRVNAME}
[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now ${SRVNAME}

echo
echo "${SRVNAME} is enabled and running. Check it with:"
echo "  systemctl status ${SRVNAME}"
echo "  journalctl -u ${SRVNAME} -f"
