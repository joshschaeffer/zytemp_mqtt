FROM alpine:3.24

# Alpine's hidapi ships both the hidraw and the libusb build
RUN apk add --no-cache python3 py3-paho-mqtt py3-yaml hidapi

WORKDIR /opt/zytempmqtt
COPY zytempmqtt/ zytempmqtt/
ENTRYPOINT ["python3", "-m", "zytempmqtt"]
