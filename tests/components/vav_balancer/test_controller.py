"""Controller behaviour tests, run against the real hass test fixture."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vav_balancer import controller as controller_mod
from custom_components.vav_balancer.const import (
    CONF_EXHAUST_FANS,
    CONF_EXHAUST_PROFILES,
    CONF_INTAKE_FANS,
    CONF_INTAKE_PROFILES,
    CONF_MAX_CORRECTION_SECONDS,
    CONF_MIN_HOME_PCT,
    CONF_MIN_HOME_STEP,
)
from custom_components.vav_balancer.controller import VAVController


def _entry(data: dict) -> MockConfigEntry:
    entry = MockConfigEntry(domain="vav_balancer", data=data)
    return entry

def _mock_fan_services(hass: HomeAssistant) -> list[tuple[str, dict]]:
    """Register fake fan.* services and return the list of calls made to them."""
    calls: list[tuple[str, dict]] = []

    def _make_handler(service: str):
        async def _handler(call) -> None:
            calls.append((service, dict(call.data)))
        return _handler

    for service in ("turn_off", "set_percentage", "set_preset_mode"):
        hass.services.async_register("fan", service, _make_handler(service))
    return calls



async def test_dynamic_threshold_entity_resolved(hass: HomeAssistant) -> None:
    hass.states.async_set("fan.in1", "on", {"percentage": 90})
    hass.states.async_set("fan.ex1", "on", {"percentage": 0})
    hass.states.async_set("sensor.bath_humidity", "60")
    hass.states.async_set("sensor.avg_humidity", "55")

    entry = _entry({
        CONF_INTAKE_FANS: ["fan.in1"],
        CONF_EXHAUST_FANS: ["fan.ex1"],
        CONF_INTAKE_PROFILES: {
            "fan.in1": {"control_type": "percentage", "max_airflow": 500, "rules": []},
        },
        CONF_EXHAUST_PROFILES: {
            "fan.ex1": {
                "control_type": "percentage", "max_airflow": 550,
                "rules": [{
                    "entity_id": "sensor.bath_humidity", "mode": "above",
                    "threshold_entity": "sensor.avg_humidity", "output": 80,
                }],
            },
        },
    })
    entry.add_to_hass(hass)
    controller = VAVController(hass, entry)
    controller._recalculate("test")

    debug = controller.rule_debug_for("fan.ex1")[0]
    assert debug["threshold"] == 55.0
    assert debug["threshold_source"] == "sensor.avg_humidity"
    assert debug["active"] is True


async def test_pressure_correction_tracks_actual_intake(hass: HomeAssistant) -> None:
    """Urgent exhaust correction must not overshoot past actual intake."""
    hass.states.async_set(
        "fan.in1", "on",
        {"preset_mode": "5", "preset_modes": [str(i) for i in range(1, 7)]},
    )
    hass.states.async_set("fan.ex1", "on", {"percentage": 70.98})

    entry = _entry({
        CONF_INTAKE_FANS: ["fan.in1"],
        CONF_EXHAUST_FANS: ["fan.ex1"],
        CONF_INTAKE_PROFILES: {
            "fan.in1": {
                "control_type": "steps",
                "airflow_map": [0, 30, 45, 60, 75, 90, 140],
                "rules": [],
            },
        },
        CONF_EXHAUST_PROFILES: {
            "fan.ex1": {"control_type": "percentage", "max_airflow": 550, "rules": []},
        },
        CONF_MIN_HOME_STEP: 2,
        CONF_MIN_HOME_PCT: 10,
    })
    entry.add_to_hass(hass)
    controller = VAVController(hass, entry)
    controller._recalculate("test")

    assert "intake_capped" not in controller.plan.notes
    assert controller.actual_intake - controller.actual_exhaust < -15

    calls = _mock_fan_services(hass)
    await controller._async_execute()

    ex_calls = [c for c in calls if c[1].get("entity_id") == "fan.ex1"]
    assert ex_calls
    sent_pct = ex_calls[-1][1]["percentage"]
    sent_flow = sent_pct / 100 * 550
    # The correction must land close to *actual* intake (within tolerance,
    # plus a little rounding slack for the whole-percent service call),
    # not snap straight to the long-run plan target while intake is still
    # far from it in either direction.
    assert abs(controller.actual_intake - sent_flow) <= 15 + 6


async def test_stuck_watchdog_forces_correction(hass: HomeAssistant) -> None:
    hass.states.async_set(
        "fan.in1", "on",
        {"preset_mode": "6", "preset_modes": [str(i) for i in range(1, 7)]},
    )
    hass.states.async_set("fan.ex1", "on", {"percentage": 0})

    entry = _entry({
        CONF_INTAKE_FANS: ["fan.in1"],
        CONF_EXHAUST_FANS: ["fan.ex1"],
        CONF_INTAKE_PROFILES: {
            "fan.in1": {
                "control_type": "steps",
                "airflow_map": [0, 30, 45, 60, 75, 90, 140],
                "rules": [],
            },
        },
        CONF_EXHAUST_PROFILES: {
            "fan.ex1": {"control_type": "percentage", "max_airflow": 550, "rules": []},
        },
        CONF_MIN_HOME_STEP: 1,
        CONF_MIN_HOME_PCT: 0,
        CONF_MAX_CORRECTION_SECONDS: 60,
    })
    entry.add_to_hass(hass)
    controller = VAVController(hass, entry)

    calls = _mock_fan_services(hass)

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    now = {"t": base}
    controller_mod.dt_util.utcnow = lambda: now["t"]

    controller._recalculate("tick1")
    await controller._async_execute()

    now["t"] = base + timedelta(seconds=10)
    controller._recalculate("tick2")
    await controller._async_execute()

    now["t"] = base + timedelta(seconds=80)  # > 60s since the stuck timer started
    controller._recalculate("tick3")
    calls.clear()
    await controller._async_execute()

    in1_calls = [c for c in calls if c[1].get("entity_id") == "fan.in1"]
    assert in1_calls, "expected a forced correction once past max_correction_seconds"
    target_level = controller.target_for("fan.in1").level
    preset = in1_calls[-1][1].get("preset_mode")
    assert preset is not None
    assert int(preset) == int(round(target_level))


async def test_hysteresis_suppresses_chattering(hass: HomeAssistant) -> None:
    hass.states.async_set("fan.in1", "on", {"percentage": 50})
    hass.states.async_set("fan.ex1", "on", {"percentage": 10})
    hass.states.async_set("sensor.avg_humidity", "44")
    hass.states.async_set("sensor.bath_humidity", "44")

    def build(hysteresis: float) -> VAVController:
        rule = {
            "entity_id": "sensor.bath_humidity", "mode": "linear",
            "threshold_entity": "sensor.avg_humidity", "threshold_high": 80,
            "output": 100, "output_low": 20,
        }
        if hysteresis:
            rule["hysteresis"] = hysteresis
        entry = _entry({
            CONF_INTAKE_FANS: ["fan.in1"],
            CONF_EXHAUST_FANS: ["fan.ex1"],
            CONF_INTAKE_PROFILES: {
                "fan.in1": {"control_type": "percentage", "max_airflow": 500, "rules": []},
            },
            CONF_EXHAUST_PROFILES: {
                "fan.ex1": {"control_type": "percentage", "max_airflow": 550, "rules": [rule]},
            },
            CONF_MIN_HOME_PCT: 0,
        })
        entry.add_to_hass(hass)
        return VAVController(hass, entry)

    noise = [44.0, 43.9, 44.1, 43.8, 44.2, 43.9, 44.0, 43.7, 44.1, 44.0]

    for hysteresis, expect_flips in ((0, True), (1.0, False)):
        controller = build(hysteresis)
        flips = 0
        prev_active = None
        for value in noise:
            hass.states.async_set("sensor.bath_humidity", str(value))
            controller._recalculate("noise")
            active = controller.rule_debug_for("fan.ex1")[0]["active"]
            if prev_active is not None and active != prev_active:
                flips += 1
            prev_active = active
        if expect_flips:
            assert flips > 0
        else:
            assert flips == 0


async def test_activation_delay_debounces(hass: HomeAssistant) -> None:
    hass.states.async_set("fan.in1", "on", {"percentage": 90})
    hass.states.async_set("fan.ex1", "on", {"percentage": 0})
    hass.states.async_set("binary_sensor.presence", "off")

    entry = _entry({
        CONF_INTAKE_FANS: ["fan.in1"],
        CONF_EXHAUST_FANS: ["fan.ex1"],
        CONF_INTAKE_PROFILES: {
            "fan.in1": {"control_type": "percentage", "max_airflow": 500, "rules": []},
        },
        CONF_EXHAUST_PROFILES: {
            "fan.ex1": {
                "control_type": "percentage", "max_airflow": 550,
                "rules": [{
                    "entity_id": "binary_sensor.presence", "mode": "on",
                    "output": 80, "delay_seconds": 30,
                }],
            },
        },
    })
    entry.add_to_hass(hass)
    controller = VAVController(hass, entry)

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    now = {"t": base}
    controller_mod.dt_util.utcnow = lambda: now["t"]

    controller._recalculate("baseline")
    hass.states.async_set("binary_sensor.presence", "on")
    controller._recalculate("flip")
    assert controller.rule_debug_for("fan.ex1")[0]["active"] is False

    now["t"] = base + timedelta(seconds=10)
    controller._recalculate("still pending")
    assert controller.rule_debug_for("fan.ex1")[0]["active"] is False

    now["t"] = base + timedelta(seconds=31)
    controller._recalculate("elapsed")
    assert controller.rule_debug_for("fan.ex1")[0]["active"] is True


async def test_night_and_day_ceiling_applied(hass: HomeAssistant) -> None:
    from custom_components.vav_balancer.const import CONF_NIGHT_END, CONF_NIGHT_START

    hass.states.async_set("fan.in1", "on", {"percentage": 10})
    entry = _entry({
        CONF_INTAKE_FANS: ["fan.in1"],
        CONF_EXHAUST_FANS: [],
        CONF_INTAKE_PROFILES: {
            "fan.in1": {
                "control_type": "steps",
                "airflow_map": [0, 30, 45, 60, 75, 90, 140],
                "day_max": 5, "night_max": 2, "rules": [],
            },
        },
        CONF_MIN_HOME_STEP: 6,  # force demand to the very top to test the clamp
    })
    entry.add_to_hass(hass)
    controller = VAVController(hass, entry)

    controller._is_night = lambda: False
    controller._recalculate("day")
    assert controller.target_for("fan.in1").level == 5

    controller._is_night = lambda: True
    controller._recalculate("night")
    assert controller.target_for("fan.in1").level == 2
