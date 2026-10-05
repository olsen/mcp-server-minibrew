"""Live device telemetry from MiniBrew's MQTT broker (what the portal's live view reads).

The REST API has no fan, Peltier or internal-temperature readings; devices publish
them as protobuf messages on ``devices/logs/<uuid>`` at broker.minibrew.io, MQTT 3.1.1
over a TLS WebSocket. The portal logs in as ``breweryportal-<user uuid>`` with the
REST bearer token as password.

This is a minimal, subscribe-only client on the standard library: it sends CONNECT,
SUBSCRIBE to device-log topics, PINGREQ and DISCONNECT, and nothing else. It never
publishes, so it cannot command a device.

The protobuf schema is private. Field numbers and the sensor/process enums come from
stuartp44/pymbrewclient (reconstructed from captured traffic and the portal's
compiled protobuf client); unknown measurement ids are passed through by number.
Process codes are labelled with labels.py's tables.
"""

from __future__ import annotations

import base64
import os
import re
import socket
import struct
import time
import uuid

import httpx

from .client import MiniBrewError
from .normalize import iso

BROKER_HOST = "broker.minibrew.io"
BROKER_PORT = 15675
WS_PATH = "/ws"
KEEPALIVE = 60

# Device uuids look like "1234A5678-ABCD1234"; no MQTT wildcards or separators.
DEVICE_UUID = re.compile(r"[A-Za-z0-9-]{4,64}")

# SensorType: the keys of a telemetry message's repeated measurements.
SENSORS = {
    0: "temp_environment",
    1: "temp_control_out",
    2: "temp_control_in",
    3: "temp_liquid",
    4: "temp_peltier",
    6: "pump_current",
    7: "carousel_position",
    9: "top_connection_present",
    10: "bottom_connection_present",
    12: "carousel_zero_position",
    13: "esp_core_temp",
    14: "barometric_pressure",
    15: "valve_mash_tun_open",
    16: "valve_boiling_kettle_open",
    17: "valve_water_inlet_open",
    18: "system_temp",
    19: "button",
    20: "pcb_fan",
    21: "gsensor_temp",
    22: "gsensor_gravity",
    23: "gsensor_battery",
    24: "temp_control_power",  # Peltier power %, signed: negative = cooling
    25: "liquid_flow_power",
    26: "peltier_fan_power",  # fan duty %
    27: "gsensor_rssi",  # an external gravity sensor's signal; 0 when none is paired
}

# --- protobuf wire format -------------------------------------------------------


def _varint(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift >= 64:
            break
    raise ValueError("bad varint")


def decode_fields(data: bytes) -> dict[int, list]:
    """Raw protobuf fields: {number: [int | 4/8-byte bytes | length-delimited bytes]}."""
    out: dict[int, list] = {}
    pos = 0
    while pos < len(data):
        tag, pos = _varint(data, pos)
        number, wire = tag >> 3, tag & 7
        if wire == 0:
            value, pos = _varint(data, pos)
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            value, pos = data[pos : pos + size], pos + size
        elif wire == 2:
            size, pos = _varint(data, pos)
            value, pos = data[pos : pos + size], pos + size
        else:
            raise ValueError(f"unknown wire type {wire}")
        if pos > len(data):
            raise ValueError("truncated field")
        out.setdefault(number, []).append(value)
    return out


def _int(fields: dict, n: int) -> int | None:
    v = fields.get(n, [None])[0]
    return v if isinstance(v, int) else None


def _float(fields: dict, n: int) -> float | None:
    v = fields.get(n, [None])[0]
    if isinstance(v, bytes) and len(v) == 4:
        return round(struct.unpack("<f", v)[0], 2)
    return None


def _bytes(fields: dict, n: int) -> bytes | None:
    v = fields.get(n, [None])[0]
    return v if isinstance(v, bytes) else None


def decode_device_log(payload: bytes) -> dict:
    """Decode one devices/logs message into named fields (None where absent).

    Live messages wrap the telemetry in an envelope {1: sequence, 3: telemetry,
    4: session id, 5: epoch ms}; older captures are the bare telemetry message.
    """
    outer = decode_fields(payload)
    inner = _bytes(outer, 3)
    if _int(outer, 5) is not None and inner is not None:
        tel, session, ts = decode_fields(inner), _int(outer, 4), _int(outer, 5)
    else:
        tel, session, ts = outer, _int(outer, 11), _int(outer, 1)
    measurements: dict = {}
    for entry in tel.get(3, []):
        if isinstance(entry, bytes):
            m = decode_fields(entry)
            key, value = _int(m, 1), _float(m, 2)
            if key is not None and value is not None:
                measurements[SENSORS.get(key, key)] = value
    state = decode_fields(_bytes(tel, 2) or b"")
    return {
        "time": iso(ts, millis=True) if ts else None,
        "session_id": session or None,
        "current_state": _int(state, 1),
        "process_type": _int(state, 2),
        "process_state": _int(state, 3),
        "user_action": _int(state, 8),
        "process_phase": _int(tel, 21),
        "machine_type": _int(tel, 22),
        "target_temp": _float(tel, 18),
        "current_temp": _float(tel, 19),
        "seconds_until_next_action": _int(tel, 26),
        "measurements": measurements,
    }


# --- MQTT 3.1.1 over WebSocket, subscribe only ----------------------------------


def _mqtt_str(s: str) -> bytes:
    b = s.encode()
    return struct.pack("!H", len(b)) + b


def _mqtt_packet(kind: int, body: bytes) -> bytes:
    length, size = b"", len(body)
    while True:
        byte, size = size % 128, size // 128
        length += bytes([byte | (0x80 if size else 0)])
        if not size:
            return bytes([kind]) + length + body


class _WebSocket:
    """Just enough RFC 6455 for binary MQTT frames over TLS."""

    def __init__(self, sock: socket.socket) -> None:
        self.sock = sock
        self.buf = b""

    @classmethod
    def connect(cls, host: str, port: int, path: str, timeout: float) -> _WebSocket:
        raw = socket.create_connection((host, port), timeout=timeout)
        sock = httpx.create_ssl_context().wrap_socket(raw, server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall(
            (
                f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                "Sec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: mqtt\r\n\r\n"
            ).encode()
        )
        ws = cls(sock)
        while b"\r\n\r\n" not in ws.buf:
            ws._fill()
        head, ws.buf = ws.buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise MiniBrewError(f"MQTT broker refused the WebSocket: {head[:80]!r}")
        return ws

    def _fill(self) -> None:
        chunk = self.sock.recv(65536)
        if not chunk:
            raise ConnectionError("broker closed the connection")
        self.buf += chunk

    def _need(self, n: int) -> None:
        while len(self.buf) < n:
            self._fill()

    def send(self, data: bytes, opcode: int = 0x2) -> None:
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            head = struct.pack("!BB", 0x80 | opcode, 0x80 | n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | opcode, 0xFE, n)
        else:
            head = struct.pack("!BBQ", 0x80 | opcode, 0xFF, n)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> bytes:
        """The next data frame's payload; answers pings, raises on close.

        A frame is only consumed once it is complete, so a socket timeout can't
        leave the stream mid-frame.
        """
        while True:
            self._need(2)
            b0, b1 = self.buf[0], self.buf[1]
            n, pos = b1 & 0x7F, 2
            if n == 126:
                self._need(4)
                (n,), pos = struct.unpack("!H", self.buf[2:4]), 4
            elif n == 127:
                self._need(10)
                (n,), pos = struct.unpack("!Q", self.buf[2:10]), 10
            mask = None
            if b1 & 0x80:
                self._need(pos + 4)
                mask, pos = self.buf[pos : pos + 4], pos + 4
            self._need(pos + n)
            data, self.buf = self.buf[pos : pos + n], self.buf[pos + n :]
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            opcode = b0 & 0x0F
            if opcode == 0x8:
                raise ConnectionError("broker closed the WebSocket")
            if opcode == 0x9:
                self.send(data, 0xA)
            elif opcode in (0x0, 0x1, 0x2):
                return data

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


class _Mqtt:
    def __init__(self, ws: _WebSocket) -> None:
        self.ws = ws
        self.buf = b""

    def read(self) -> tuple[int, bytes]:
        """The next packet as (first byte, body), consumed only once complete."""
        while True:
            size = shift = 0
            for pos in range(1, min(len(self.buf), 5)):
                size |= (self.buf[pos] & 0x7F) << shift
                shift += 7
                if not self.buf[pos] & 0x80:
                    end = pos + 1 + size
                    if len(self.buf) >= end:
                        kind, body, self.buf = self.buf[0], self.buf[pos + 1 : end], self.buf[end:]
                        return kind, body
                    break
            self.buf += self.ws.recv()


def capture(token: str, user_uuid: str, devices: list[str], seconds: float) -> dict[str, list]:
    """Listen to the devices' log topics for ``seconds``; {uuid: [decoded message, ...]}."""
    for d in devices:
        if not DEVICE_UUID.fullmatch(d):
            raise MiniBrewError(f"invalid device uuid: {d!r}")
    out: dict[str, list] = {d: [] for d in devices}
    deadline = time.monotonic() + seconds
    try:
        ws = _WebSocket.connect(BROKER_HOST, BROKER_PORT, WS_PATH, timeout=15)
    except (OSError, ConnectionError) as e:
        raise MiniBrewError(f"can't reach the MiniBrew MQTT broker: {e}") from e
    try:
        mqtt = _Mqtt(ws)
        body = (
            _mqtt_str("MQTT")
            + bytes([4, 0xC2])  # protocol level 4; username, password, clean session
            + struct.pack("!H", KEEPALIVE)
            + _mqtt_str(f"breweryportal-{uuid.uuid4()}")
            + _mqtt_str(f"breweryportal-{user_uuid}")
            + _mqtt_str(token)
        )
        ws.send(_mqtt_packet(0x10, body))
        kind, ack = mqtt.read()
        if kind >> 4 != 2 or len(ack) < 2 or ack[1] != 0:
            code = ack[1] if len(ack) > 1 else None
            reason = "bad credentials (expired token?)" if code in (4, 5) else f"code {code}"
            raise MiniBrewError(f"MQTT broker refused the login: {reason}")
        topics = b"".join(_mqtt_str(f"devices/logs/{d}") + b"\x00" for d in devices)
        ws.send(_mqtt_packet(0x82, struct.pack("!H", 1) + topics))
        last_ping = time.monotonic()
        while (left := deadline - time.monotonic()) > 0:
            ws.sock.settimeout(min(left, 20))
            try:
                kind, data = mqtt.read()
            except TimeoutError:
                continue
            finally:
                if time.monotonic() - last_ping > KEEPALIVE / 2:
                    ws.send(_mqtt_packet(0xC0, b""))
                    last_ping = time.monotonic()
            if kind >> 4 != 3:  # only PUBLISH carries telemetry
                continue
            (tlen,) = struct.unpack("!H", data[:2])
            topic = data[2 : 2 + tlen].decode(errors="replace")
            payload = data[2 + tlen + (2 if kind & 0x06 else 0) :]
            device = topic.removeprefix("devices/logs/")
            if device in out:
                try:
                    out[device].append(decode_device_log(payload))
                except ValueError:
                    pass  # an undecodable message; skip it
        try:
            ws.send(_mqtt_packet(0xE0, b""))
        except OSError:
            pass
    except (OSError, ConnectionError) as e:
        if not any(out.values()):
            raise MiniBrewError(f"MQTT stream failed: {e}") from e
    finally:
        ws.close()
    return out
