"""Setup/unload lifecycle tests for the integration entry point."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vav_balancer.const import (
    CONF_EXHAUST_FANS,
    CONF_EXHAUST_PROFILES,
    CONF_INTAKE_FANS,
    CONF_INTAKE_PROFILES,
)


async def test_setup_and_unload(hass: HomeAssistant) -> None:
    hass.states.async_set("fan.in1", "on", {"percentage": 50})
    hass.states.async_set("fan.ex1", "on", {"percentage": 10})
    entry = MockConfigEntry(
        domain="vav_balancer",
        data={
            CONF_INTAKE_FANS: ["fan.in1"],
            CONF_EXHAUST_FANS: ["fan.ex1"],
            CONF_INTAKE_PROFILES: {
                "fan.in1": {"control_type": "percentage", "max_airflow": 500, "rules": []},
            },
            CONF_EXHAUST_PROFILES: {
                "fan.ex1": {"control_type": "percentage", "max_airflow": 550, "rules": []},
            },
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state == ConfigEntryState.LOADED
    assert entry.runtime_data is not None
    assert hass.states.get("fan.vav_balancer") is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state == ConfigEntryState.NOT_LOADED


async def test_setup_fails_with_no_valid_fans(hass: HomeAssistant) -> None:
    """quality scale: test-before-setup."""
    entry = MockConfigEntry(
        domain="vav_balancer",
        data={
            CONF_INTAKE_FANS: [],
            CONF_EXHAUST_FANS: [],
            CONF_INTAKE_PROFILES: {},
            CONF_EXHAUST_PROFILES: {},
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert result is False
    assert entry.state == ConfigEntryState.SETUP_ERROR


async def test_setup_fails_when_every_profile_is_invalid(hass: HomeAssistant) -> None:
    """A fan listed but with a broken profile is skipped, not fatal on its
    own -- but if that leaves zero usable fans, setup must still fail."""
    entry = MockConfigEntry(
        domain="vav_balancer",
        data={
            CONF_INTAKE_FANS: ["fan.in1"],
            CONF_EXHAUST_FANS: [],
            CONF_INTAKE_PROFILES: {
                "fan.in1": {"control_type": "steps", "airflow_map": [30, 0]},  # decreasing: invalid
            },
            CONF_EXHAUST_PROFILES: {},
        },
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert result is False
    assert entry.state == ConfigEntryState.SETUP_ERROR
