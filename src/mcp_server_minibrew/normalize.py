"""Project MiniBrew API objects down to compact dicts for the model.

Numeric codes become labels (see labels.py), epoch timestamps become ISO 8601
UTC, and missing/None fields are omitted rather than returned as null.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .labels import (
    COMMAND_ERRORS,
    CONNECTION_STATUSES,
    DEVICE_STATES,
    DEVICE_TYPES,
    FAILED_STATES,
    PROCESS_PHASES,
    PROCESS_STATES,
    PROCESS_TYPES,
    SESSION_STATUSES,
    SESSION_TYPES,
    STATE_TO_PHASE,
    USER_ACTIONS,
    label,
)


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None and v != [] and v != {}}


def iso(ts: float | None, millis: bool = False) -> str | None:
    if ts is None:
        return None
    if millis:
        ts = ts / 1000
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _num(value):
    """MiniBrew sends most decimals as strings ("68.0"); return a number when possible."""
    if isinstance(value, str):
        try:
            f = float(value)
        except ValueError:
            return value
        return int(f) if f.is_integer() else f
    return value


def device_state(dev: dict) -> dict:
    """The live state fields of a /devices/ object (also embedded in sessions)."""
    state = dev.get("process_state")
    action = dev.get("user_action")
    return _drop_none(
        {
            "state": label(DEVICE_STATES, dev.get("current_state")),
            "process_state": label(PROCESS_STATES, state),
            "phase": STATE_TO_PHASE.get(state),
            "failed": True if state in FAILED_STATES else None,
            "user_action": label(USER_ACTIONS, action) if action else None,
            "user_action_id": action or None,
            "online": dev["connection_status"] == 1 if "connection_status" in dev else None,
            # online is true/false; this adds the third status, 2 = "not responding".
            "connection": CONNECTION_STATUSES.get(2) if dev.get("connection_status") == 2 else None,
            "last_online": dev.get("last_time_online"),
            "process": label(PROCESS_TYPES, dev.get("process_type")),
            "state_since": dev.get("last_process_state_change"),
            "firmware": (dev.get("software_version") or "").split(",")[0] or None,
            "updating": dev.get("updating") or None,
        }
    )


def overview_device(dev: dict, bucket: str, live: dict | None = None) -> dict:
    """A breweryoverview entry, merged with its /devices/ state when available."""
    out = {
        "uuid": dev.get("uuid"),
        "name": dev.get("title"),
        "type": label(DEVICE_TYPES, dev.get("device_type")),
        "bucket": bucket,
        "online": dev.get("online"),
        "stage": dev.get("stage"),
        "session_id": dev.get("session_id"),
        "session_status": label(SESSION_STATUSES, dev.get("session_status")),
        "beer": dev.get("beer_name"),
        "style": dev.get("beer_style"),
        "recipe_version": dev.get("recipe_version"),
        "current_temp": dev.get("current_temp"),
        "target_temp": dev.get("target_temp"),
        "gravity": dev.get("gravity") if dev.get("gravity") not in (None, "1.00") else None,
        "status_time_s": dev.get("status_time"),
        "needs_cleaning": dev.get("needs_cleaning") or None,
        "needs_acid_cleaning": dev.get("needs_acid_cleaning") or None,
        "uncleaned_brews": dev.get("consecutive_uncleaned_brews") or None,
        "last_cip": dev.get("last_cip_date"),
        "latest_brew": dev.get("latest_brew_date"),
        "command_error": label(COMMAND_ERRORS, dev.get("pending_command_error") or None),
    }
    if live:
        out.update({k: v for k, v in device_state(live).items() if k != "online"})
    elif dev.get("user_action"):
        out["user_action"] = label(USER_ACTIONS, dev["user_action"])
        out["user_action_id"] = dev["user_action"]
    return _drop_none(out)


def session_summary(s: dict) -> dict:
    beer = s.get("beer") or {}
    dev = s.get("device") or {}
    return _drop_none(
        {
            "id": s.get("id"),
            "type": label(SESSION_TYPES, s.get("session_type")),
            "status": label(SESSION_STATUSES, s.get("status")),
            "beer": beer.get("name"),
            "style": beer.get("style_name"),
            "recipe_id": s.get("beer_recipe_id"),
            "recipe_version": s.get("beer_recipe_version"),
            "device": dev.get("custom_name") or dev.get("serial_number"),
            "device_type": label(DEVICE_TYPES, dev.get("device_type")),
            "created": s.get("created"),
            "modified": s.get("modified"),
            "original_gravity": _num(s.get("original_gravity")),
        }
    )


def _milestone(m: dict) -> dict:
    return _drop_none(
        {
            "time": m.get("created_at") or m.get("created"),
            "stage": m.get("stage"),
            "step": m.get("step_index"),
            "sub_type": m.get("sub_type"),
            "ingredient": m.get("ingredient_name"),
            "carousel_position": m.get("carousel_position"),
            "target_temp": _num(m.get("target_temperature")),
        }
    )


def session_detail(s: dict) -> dict:
    out = session_summary(s)
    dev = s.get("device") or {}
    if dev:
        out["device_state"] = device_state(dev)
        out["device_uuid"] = dev.get("uuid")
    furthest = s.get("furthest_status") or {}
    if furthest.get("procState") is not None:
        out["furthest_process_state"] = label(PROCESS_STATES, furthest["procState"])
    out["brew_started"] = iso(s.get("brew_timestamp"))
    out["og_measured_at"] = iso(s.get("timestamp_original_gravity"))
    out["step_timestamps"] = {
        k: iso(v, millis=True) for k, v in (s.get("actual_step_timestamps") or {}).items()
    }
    # Milestones repeat (e.g. RINSE_START twice); keep the first of each identical run.
    milestones, last = [], None
    for m in s.get("milestones") or []:
        compact = _milestone(m)
        key = {k: v for k, v in compact.items() if k != "time"}
        if key != last:
            milestones.append(compact)
        last = key
    out["milestones"] = milestones
    if s.get("pending_command_error"):
        out["command_error"] = label(COMMAND_ERRORS, s["pending_command_error"])
    return _drop_none(out)


def user_action_steps(ua: dict) -> dict:
    steps = sorted(ua.get("action_steps") or [], key=lambda a: a.get("order") or 0)
    return _drop_none(
        {
            "title": ua.get("title"),
            "steps": [
                _drop_none({"title": a.get("title"), "description": a.get("description")})
                for a in steps
            ],
        }
    )


def recipe_summary(r: dict) -> dict:
    return _drop_none(
        {
            "id": r.get("id"),
            "beer_id": r.get("beer_id"),
            "beer": r.get("beer_name"),
            "version": r.get("version_name"),
            "og": _num(r.get("og")),
            "fg": _num(r.get("fg")),
            "abv": _num(r.get("abv")),
            "ibu": _num(r.get("ibu")),
            "srm": _num(r.get("srm")),
            "times_brewed": r.get("times_brewed"),
            "brewable": r.get("brewable"),
            "modified": iso(r.get("modified_timestamp")),
        }
    )


def beer_summary(b: dict) -> dict:
    style = b.get("style") or {}
    return _drop_none(
        {
            "id": b.get("id"),
            "name": b.get("name"),
            # A list item embeds the style; a create response may give only its id.
            "style": style.get("name") if isinstance(style, dict) else None,
            "abv": b.get("abv"),
            "ibu": b.get("ibu"),
            "srm": b.get("srm"),
            "latest_brew": b.get("latest_brew_date"),
            "modified": b.get("modified"),
        }
    )


def ingredient(item: dict) -> dict:
    """A catalogue ingredient (hop, fermentable, yeast, adjunct): scalar fields only."""
    return _drop_none(
        {
            k: v
            for k, v in item.items()
            if not isinstance(v, dict | list)
            and v != ""
            and "image" not in k
            and k not in ("created", "modified", "uuid")
        }
    )


def _peltier_mode(power: float | None) -> str | None:
    if power is None:
        return None
    return "cooling" if power < 0 else "heating" if power > 0 else "idle"


def _cooling_observations(c: dict) -> list[str]:
    """Plain-language flags for the cooling system; conservative on purpose."""
    power, fan = c.get("peltier_power_pct"), c.get("fan_duty_pct")
    liquid, target = c.get("temp_liquid"), c.get("target_temp")
    peltier, ambient = c.get("temp_peltier"), c.get("temp_environment")
    out = []
    if power is not None and abs(power) >= 10 and not fan:
        reads = f"{fan:g}%" if fan is not None else "nothing"
        out.append(
            f"Peltier is at {abs(power):g}% but the fan reads {reads}: "
            "the fan may be stalled, unplugged or failing."
        )
    if power is not None and power <= -90 and liquid is not None and target is not None:
        if liquid - target > 1:
            note = f" with the room at {ambient:g}°C" if ambient is not None else ""
            out.append(
                f"Cooling flat out and still {liquid - target:.1f}°C above target{note}: either "
                "the target is too far below room temperature, or heat isn't leaving the "
                "heatsink (dust, blocked vents, weak fan)."
            )
    if power is not None and power < -10 and None not in (peltier, liquid) and peltier >= liquid:
        out.append(
            f"While cooling, the Peltier sensor ({peltier:g}°C) is no colder than the beer "
            f"({liquid:g}°C): the cold side isn't pulling heat out."
        )
    return out


def telemetry_summary(dev: dict, samples: list[dict]) -> dict:
    """Live MQTT telemetry for one device: the latest reading, the cooling system with
    plain-language observations, and min/max of readings that moved during the capture."""
    head = {
        "uuid": dev.get("uuid"),
        "name": dev.get("custom_name") or dev.get("serial_number"),
        "type": label(DEVICE_TYPES, dev.get("device_type")),
        "samples": len(samples),
    }
    if not samples:
        return head
    last = samples[-1]
    m = dict(last["measurements"])
    # Gravity-sensor channels read 0 when no sensor is paired.
    if not any(m.get(k) for k in ("gsensor_gravity", "gsensor_battery", "gsensor_rssi")):
        for k in ("gsensor_temp", "gsensor_gravity", "gsensor_battery", "gsensor_rssi"):
            m.pop(k, None)
    power = m.pop("temp_control_power", None)
    cooling = _drop_none(
        {
            "peltier_power_pct": power,
            "peltier_mode": _peltier_mode(power),
            "fan_duty_pct": m.pop("peltier_fan_power", None),
            "pcb_fan": m.pop("pcb_fan", None),
            "target_temp": last.get("target_temp"),
            "temp_liquid": m.pop("temp_liquid", last.get("current_temp")),
            "temp_peltier": m.pop("temp_peltier", None),
            "temp_environment": m.pop("temp_environment", None),
            "temp_control_in": m.pop("temp_control_in", None),
            "temp_control_out": m.pop("temp_control_out", None),
        }
    )
    ranges = {}
    for key in {k for s in samples for k in s["measurements"]}:
        values = [s["measurements"][key] for s in samples if key in s["measurements"]]
        if len(set(values)) > 1:
            ranges[SENSOR_ALIASES.get(key, key)] = {"min": min(values), "max": max(values)}
    action = last.get("user_action")
    return _drop_none(
        {
            **head,
            "first": samples[0].get("time"),
            "last": last.get("time"),
            "session_id": last.get("session_id"),
            "process": label(PROCESS_TYPES, last.get("process_type")),
            "phase": label(PROCESS_PHASES, last.get("process_phase") or None),
            "process_state": label(PROCESS_STATES, last.get("process_state")),
            "user_action": label(USER_ACTIONS, action) if action else None,
            "seconds_until_next_action": last.get("seconds_until_next_action"),
            "cooling": cooling,
            "observations": _cooling_observations(cooling),
            "other_readings": m,
            "ranges": ranges,
        }
    )


# Names used in telemetry_summary's cooling block, for the matching ranges entries.
SENSOR_ALIASES = {"temp_control_power": "peltier_power_pct", "peltier_fan_power": "fan_duty_pct"}
