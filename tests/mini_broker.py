"""
A minimal MQTT 3.1.1 broker, just capable enough to test client behaviour:
CONNECT/CONNACK, PUBLISH, SUBSCRIBE/SUBACK, PINGREQ/PINGRESP and DISCONNECT.

Stopping and starting one on the same port simulates a broker restart, which
is what discards retained messages when the broker does not persist them.
"""

import socket
import threading

CONNECT, PUBLISH, SUBSCRIBE, PINGREQ, DISCONNECT = 1, 3, 8, 12, 14


class MiniBroker:
    """Accepts connections and records what gets published to it."""

    # Sent in reply to CONNECT: CONNACK, remaining length 2, no flags, accepted
    CONNACK = b'\x20\x02\x00\x00'
    PINGRESP = b'\xd0\x00'

    def __init__(self, port=0):
        self._requested_port = port
        self.port = port
        self.published = []      # (topic, payload, retain)
        self.accepts = 0         # how many TCP connections were accepted
        self._sock = None
        self._thread = None
        self._running = False
        self._conns = []

    def start(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(('127.0.0.1', self._requested_port))
        # With port 0 the OS picks one; publish it so a restart can rebind it
        self.port = self._sock.getsockname()[1]
        self._sock.listen(5)
        self._sock.settimeout(0.2)
        self._running = True
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        """Go away as abruptly as a restarting broker does."""
        self._running = False
        for c in self._conns:
            try:
                c.close()
            except OSError:
                pass
        self._conns = []
        try:
            self._sock.close()
        except OSError:
            pass
        if self._thread:
            self._thread.join(timeout=2)
            self._thread = None

    def topics(self, suffix=None):
        return [t for t, _, _ in self.published
                if suffix is None or t.endswith(suffix)]

    def _accept_loop(self):
        while self._running:
            try:
                conn, _ = self._sock.accept()
            except (socket.timeout, OSError):
                continue
            self.accepts += 1
            self._conns.append(conn)
            threading.Thread(target=self._handle, args=(conn,),
                             daemon=True).start()

    def _read_exact(self, conn, n):
        buf = b''
        while len(buf) < n:
            chunk = conn.recv(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    def _read_packet(self, conn):
        hdr = self._read_exact(conn, 1)
        if hdr is None:
            return None, None
        mult, length = 1, 0
        while True:                       # remaining length is a varint
            b = self._read_exact(conn, 1)
            if b is None:
                return None, None
            length += (b[0] & 0x7F) * mult
            if not (b[0] & 0x80):
                break
            mult *= 128
        body = self._read_exact(conn, length) if length else b''
        if body is None:
            return None, None
        return hdr[0], body

    def _handle(self, conn):
        conn.settimeout(0.5)
        while self._running:
            try:
                ptype, body = self._read_packet(conn)
            except (socket.timeout, OSError):
                continue
            if ptype is None:
                break
            kind = ptype >> 4
            try:
                if kind == CONNECT:
                    conn.sendall(self.CONNACK)
                elif kind == PUBLISH:
                    tlen = (body[0] << 8) | body[1]
                    self.published.append((
                        body[2:2 + tlen].decode('utf-8', 'replace'),
                        body[2 + tlen:].decode('utf-8', 'replace'),
                        bool(ptype & 0x01),      # retain flag
                    ))
                elif kind == SUBSCRIBE:
                    conn.sendall(b'\x90\x03' + body[0:2] + b'\x00')
                elif kind == PINGREQ:
                    conn.sendall(self.PINGRESP)
                elif kind == DISCONNECT:
                    self.saw_disconnect = True
                    break
            except OSError:
                break
        try:
            conn.close()
        except OSError:
            pass

    saw_disconnect = False


class RejectingBroker(MiniBroker):
    """Answers CONNECT with 'bad user name or password'.

    Reachable, but turns the client away - which is a different failure from
    the broker being unreachable, and has to look different in the log.
    """

    CONNACK = b'\x20\x02\x00\x04'


class RefusingBroker(MiniBroker):
    """Accepts the TCP connection then hangs up without a CONNACK.

    Nothing listening at all would also refuse, but then there is no way to
    count how often the client retried - which is the point of this class.
    """

    def _handle(self, conn):
        try:
            conn.close()
        except OSError:
            pass
