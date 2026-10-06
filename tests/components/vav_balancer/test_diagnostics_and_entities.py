"""Diagnostics output and entity availability/translation behaviour."""
from __future__ import annotations

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vav_balancer.diagnostics import (
    async_get_config_entry_diagnostics,
)


async def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    hass.states.async_set("fan.in1", "on", {"percentage": 50})
    hass.states.async_set("fan.ex1", "on", {"percentage": 10})
    entry = MockConfigEntry(
        domain="vav_balancer",
        data={
            "intake_fans": ["fan.in1"],
            "exhaust_fans": ["fan.ex1"],
            "intake_profiles": {
                "fan.in1": {"control_type": "percentage", "max_airflow": 500, "rules": []},
            },
            "exhaust_profiles": {
                "fan.ex1": {"control_type": "percentage", "max_airflow": 550, "rules": []},
            },
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_diagnostics_contains_config_and_state(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["config"]["intake_fans"] == ["fan.in1"]
    assert "controller_state" in diag
    assert "target_intake_m3h" in diag["controller_state"]


async def test_target_sensor_unavailable_before_first_calculation(
    hass: HomeAssistant,
) -> None:
    """entity-unavailable: target_* sensors have nothing to show yet."""
    from custom_components.vav_balancer.controller import VAVController
    from custom_components.vav_balancer.sensor import VAVGlobalSensor, GLOBAL_DESCRIPTIONS

    entry = MockConfigEntry(
        domain="vav_balancer",
        data={
            "intake_fans": ["fan.in1"], "exhaust_fans": [],
            "intake_profiles": {
                "fan.in1": {"control_type": "percentage", "max_airflow": 500, "rules": []},
            },
        },
    )
    entry.add_to_hass(hass)
    controller = VAVController(hass, entry)
    # Deliberately never call controller._recalculate(): plan is None.

    description = next(d for d in GLOBAL_DESCRIPTIONS if d.key == "target_intake_flow")
    sensor = VAVGlobalSensor(controller, entry.entry_id, description)
    assert sensor.available is False
    assert sensor.native_value is None


async def test_actual_flow_sensor_unavailable_when_fan_state_unreadable(
    hass: HomeAssistant,
) -> None:
    from custom_components.vav_balancer.controller import VAVController
    from custom_components.vav_balancer.sensor import VAVFanFlowSensor

    hass.states.async_set("fan.in1", STATE_UNAVAILABLE)
    entry = MockConfigEntry(
        domain="vav_balancer",
        data={
            "intake_fans": ["fan.in1"], "exhaust_fans": [],
            "intake_profiles": {
                "fan.in1": {"control_type": "percentage", "max_airflow": 500, "rules": []},
            },
        },
    )
    entry.add_to_hass(hass)
    controller = VAVController(hass, entry)
    controller._recalculate("test")

    sensor = VAVFanFlowSensor(controller, entry.entry_id, "fan.in1", target=False)
    assert sensor.available is False
    assert sensor.native_value is None
