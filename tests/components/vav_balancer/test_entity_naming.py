"""Verify translation_key + translation_placeholders resolve correctly."""
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_fan_sensor_name_placeholder(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(
        domain="vav_balancer",
        data={
            "intake_fans": ["fan.bedroom_fan"],
            "exhaust_fans": [],
            "intake_profiles": {
                "fan.bedroom_fan": {
                    "control_type": "percentage", "max_airflow": 500, "rules": [],
                },
            },
        },
    )
    entry.add_to_hass(hass)
    hass.states.async_set("fan.bedroom_fan", "on", {"percentage": 50})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    ent_reg = hass.helpers.entity_registry if hasattr(hass, "helpers") else None
    from homeassistant.helpers import entity_registry as er
    registry = er.async_get(hass)
    entity_id = None
    for e in registry.entities.values():
        if e.unique_id.endswith("_target_flow"):
            entity_id = e.entity_id
            break
    print("found entity:", entity_id)
    assert entity_id is not None
    state = hass.states.get(entity_id)
    print("friendly_name attribute:", state.attributes.get("friendly_name"))
    assert "bedroom_fan" in state.attributes["friendly_name"]
    assert "Target flow" in state.attributes["friendly_name"]
