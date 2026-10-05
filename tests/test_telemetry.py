"""Live telemetry: protobuf decoding, MQTT framing, the summary and the tool.

Payloads are built here with a tiny protobuf encoder in the layout the devices use.
"""

from __future__ import annotations

import struct

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from mcp_server_minibrew import server, telemetry
from mcp_server_minibrew.normalize import telemetry_summary


def _varint(n: int) -> bytes:
    out = b""
    while True:
        byte, n = n & 0x7F, n >> 7
        out += bytes([byte | (0x80 if n else 0)])
        if not n:
            return out


def _field(number: int, value) -> bytes:
    if isinstance(value, float):
        return _varint(number << 3 | 5) + struct.pack("<f", value)
    if isinstance(value, bytes):
        return _varint(number << 3 | 2) + _varint(len(value)) + value
    return _varint(number << 3) + _varint(value)


def device_log(measurements: dict[int, float], *, target=19.0, current=19.3, ts=1785009523000):
    """A live devices/logs payload: envelope {1 seq, 3 telemetry, 4 session, 5 ms}."""
    state = _field(1, 1) + _field(2, 4) + _field(3, 80) + _field(8, 0)
    tel = _field(2, state)
    for key, value in measurements.items():
        tel += _field(3, _field(1, key) + _field(2, value))
    tel += _field(18, target) + _field(19, current) + _field(21, 5) + _field(26, 3600)
    return _field(1, 7) + _field(3, tel) + _field(4, 80851) + _field(5, ts)


def test_decode_device_log_reads_envelope_state_and_measurements():
    payload = device_log({0: 27.25, 3: 19.25, 24: -45.0, 26: 100.0, 99: 1.5})
    msg = telemetry.decode_device_log(payload)
    assert msg["time"] == "2026-07-25T19:58:43Z"
    assert msg["session_id"] == 80851
    assert (msg["process_type"], msg["process_state"], msg["process_phase"]) == (4, 80, 5)
    assert (msg["target_temp"], msg["current_temp"]) == (19.0, 19.3)
    assert msg["seconds_until_next_action"] == 3600
    assert msg["measurements"] == {
        "temp_environment": 27.25,
        "temp_liquid": 19.25,
        "temp_control_power": -45.0,
        "peltier_fan_power": 100.0,
        99: 1.5,  # unknown sensor ids pass through by number
    }


def test_summary_groups_cooling_and_reports_ranges():
    samples = [
        telemetry.decode_device_log(device_log({3: 19.5, 4: 18.0, 24: -45.0, 26: 100.0, 22: 0.0})),
        telemetry.decode_device_log(device_log({3: 19.25, 4: 18.0, 24: -36.0, 26: 100.0, 22: 0.0})),
    ]
    out = telemetry_summary({"uuid": "K1", "custom_name": "Keg 1", "device_type": 1}, samples)
    assert out["phase"] == "fermentation: primary"
    assert out["process_state"] == "Fermentation temperature control"
    assert out["cooling"]["peltier_mode"] == "cooling"
    assert out["cooling"]["fan_duty_pct"] == 100.0
    assert out["ranges"]["peltier_power_pct"] == {"min": -45.0, "max": -36.0}
    assert "temp_peltier" not in out["ranges"]
    assert "gsensor_gravity" not in out.get("other_readings", {})  # no gravity sensor paired
    assert "observations" not in out  # nothing wrong


def test_summary_flags_a_stalled_fan_and_cooling_that_cant_keep_up():
    msg = telemetry.decode_device_log(
        device_log({0: 28.0, 3: 22.0, 4: 23.0, 24: -100.0, 26: 0.0}, target=12.0, current=22.0)
    )
    obs = telemetry_summary({"uuid": "K1", "device_type": 1}, [msg])["observations"]
    assert any("fan reads 0%" in o for o in obs)
    assert any("10.0°C above target" in o and "28°C" in o for o in obs)
    assert any("no colder than the beer" in o for o in obs)


def test_summary_without_samples_says_so():
    assert telemetry_summary({"uuid": "K1", "device_type": 1}, []) == {
        "uuid": "K1",
        "name": None,
        "type": "keg",
        "samples": 0,
    }


class _FakeWebSocket:
    def __init__(self, frames: list[bytes]):
        self.frames = frames

    def recv(self) -> bytes:
        return self.frames.pop(0)


def test_mqtt_reader_reassembles_packets_split_across_frames():
    payload = device_log({3: 19.0})
    body = telemetry._mqtt_str("devices/logs/K1") + payload
    packet = telemetry._mqtt_packet(0x30, body)
    suback = telemetry._mqtt_packet(0x90, b"\x00\x01\x00")
    data = suback + packet
    reader = telemetry._Mqtt(_FakeWebSocket([data[:3], data[3:9], data[9:]]))
    assert reader.read() == (0x90, b"\x00\x01\x00")
    assert reader.read() == (0x30, body)


def test_capture_refuses_topic_wildcards():
    with pytest.raises(ToolError, match="invalid device uuid"):
        telemetry.capture("tok", "user", ["#"], 10)


DEVICES = [
    {"uuid": "K1-AAAA", "custom_name": "Keg 1", "device_type": 1, "connection_status": 1},
    {"uuid": "K2-BBBB", "custom_name": "Keg 2", "device_type": 1, "connection_status": 0},
    {"uuid": "B1-CCCC", "custom_name": "Olsens MiniBrew", "device_type": 0, "connection_status": 1},
]


@pytest.fixture
def captured(fake_api, monkeypatch):
    fake_api({("GET", "v1/devices/"): DEVICES, ("GET", "v1/users/me/"): {"id": 1, "uuid": "U-1"}})
    calls = []

    def fake_capture(token, user_uuid, devices, seconds):
        calls.append((token, user_uuid, devices, seconds))
        return {d: [telemetry.decode_device_log(device_log({26: 50.0}))] for d in devices[:1]}

    monkeypatch.setattr(telemetry, "capture", fake_capture)
    return calls


def test_device_telemetry_listens_to_online_devices_by_default(captured):
    out = server.get_device_telemetry(seconds=30)
    assert captured == [("tok", "U-1", ["K1-AAAA", "B1-CCCC"], 30)]
    assert [(d["name"], d["samples"]) for d in out] == [("Keg 1", 1), ("Olsens MiniBrew", 0)]
    assert out[0]["cooling"]["fan_duty_pct"] == 50.0


def test_device_telemetry_picks_by_name_or_uuid(captured):
    server.get_device_telemetry("keg 2")
    server.get_device_telemetry("b1-cccc")
    assert [c[2] for c in captured] == [["K2-BBBB"], ["B1-CCCC"]]


@pytest.mark.parametrize(
    "kwargs, message",
    [({"device": "fermenter"}, "no device matches"), ({"seconds": 5}, "seconds must be")],
)
def test_device_telemetry_validates_input(captured, kwargs, message):
    with pytest.raises(ToolError, match=message):
        server.get_device_telemetry(**kwargs)
    assert captured == []
