"""Config flow and options flow tests, driven through the real FlowManager."""
from __future__ import annotations

import json

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vav_balancer.const import DOMAIN


async def _advance_through_wizard(hass: HomeAssistant, flow_id: str) -> dict:
    """Drive a single intake + single exhaust fan through the whole wizard."""
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"intake_fans": ["fan.in1"], "exhaust_fans": ["fan.ex1"]}
    )
    assert result["step_id"] == "fan_type"

    result = await hass.config_entries.flow.async_configure(
        flow_id, {"control_type": "steps", "read_only": False}
    )
    assert result["step_id"] == "fan_template"

    result = await hass.config_entries.flow.async_configure(
       flow_id, {"template": "tion_4s"}
    )
    assert result["step_id"] == "fan_performance"

    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {"airflow_map": "0,30,45,60,75,90,140", "day_max": 5, "night_max": 2},
    )
    assert result["step_id"] == "fan_rules"

    result = await hass.config_entries.flow.async_configure(
        flow_id, {"add_another": False}
    )
    assert result["step_id"] == "fan_type"  # moved on to the exhaust fan

    result = await hass.config_entries.flow.async_configure(
        flow_id, {"control_type": "percentage"}
    )
    assert result["step_id"] == "fan_performance"

    result = await hass.config_entries.flow.async_configure(
        flow_id, {"max_airflow": 550}
    )
    assert result["step_id"] == "fan_rules"

    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {
            "entity_id": "sensor.humidity", "mode": "above", "threshold": 60,
            "output": 80, "hysteresis": 3, "delay_seconds": 10, "add_another": False,
        },
    )
    assert result["step_id"] == "global"

    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {
            "min_home_step": 1, "min_away_step": 0, "min_home_pct": 20,
            "min_away_pct": 0, "interval": 30, "pressure_tolerance": 15,
            "max_correction_seconds": 180, "boost_minutes": 15,
        },
    )
    return result


async def test_full_wizard_creates_entry(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {"wizard", "import_config"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "wizard"}
    )
    assert result["step_id"] == "wizard"  # the discovery (fan-selection) form

    final = await _advance_through_wizard(hass, result["flow_id"])

    assert final["type"] is FlowResultType.CREATE_ENTRY
    data = final["data"]
    assert data["intake_fans"] == ["fan.in1"]
    assert data["exhaust_fans"] == ["fan.ex1"]
    profile = data["intake_profiles"]["fan.in1"]
    assert profile["day_max"] == 5
    assert profile["night_max"] == 2
    rule = data["exhaust_profiles"]["fan.ex1"]["rules"][0]
    assert rule["hysteresis"] == 3
    assert rule["delay_seconds"] == 10


async def test_second_entry_is_aborted(hass: HomeAssistant) -> None:
    """unique-config-entry: only one instance allowed."""
    existing = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={})
    existing.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_discovery_rejects_overlapping_fans(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "wizard"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"intake_fans": ["fan.x"], "exhaust_fans": ["fan.x"]}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "fan_in_both_roles"


async def test_invalid_airflow_map_shows_error(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "wizard"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"intake_fans": ["fan.in1"], "exhaust_fans": []}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"control_type": "steps"}
    )
    assert result["step_id"] == "fan_template"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {}  # no template -> blank start
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"airflow_map": "30,10,5"}  # decreasing -> invalid
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "fan_performance"
    assert result["errors"]["airflow_map"] == "invalid_airflow_map"


async def test_options_export_shows_current_config(hass: HomeAssistant) -> None:
    hass.states.async_set("fan.in1", "on", {"percentage": 50})
    hass.states.async_set("fan.ex1", "on", {"percentage": 10})

    # Build via the wizard, but don't persist it as a real (unique_id-taken)
    # entry yet -- that would block a second flow in the import test below.
    # Here we only need *an* existing entry to open the options flow on.
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "wizard"}
    )
    final = await _advance_through_wizard(hass, result["flow_id"])
    entry_data = final["data"]

    # Completing the flow already registered a real entry; reuse that one
    # instead of layering a second (duplicate unique_id) MockConfigEntry
    # on top of it.
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    options_result = await hass.config_entries.options.async_init(entry.entry_id)
    assert options_result["type"] is FlowResultType.MENU
    assert set(options_result["menu_options"]) == {
        "wizard", "export_config", "import_config"
    }
    export_form = await hass.config_entries.options.async_configure(
        options_result["flow_id"], {"next_step_id": "export_config"}
    )
    assert export_form["step_id"] == "export_config"
    exported_text = export_form["data_schema"]({})["config_json"]
    exported = json.loads(exported_text)
    assert exported["intake_profiles"]["fan.in1"]["day_max"] == 5
    assert exported["exhaust_profiles"]["fan.ex1"]["rules"][0]["hysteresis"] == 3

    # Submitting the export step is a no-op (view only, no data change).
    noop = await hass.config_entries.options.async_configure(
        export_form["flow_id"], {"config_json": exported_text}
    )
    assert noop["type"] is FlowResultType.CREATE_ENTRY
    assert noop["data"] == entry_data


async def test_import_recreates_identical_config(hass: HomeAssistant) -> None:
    """Pasting a previously-exported config must reproduce it exactly,
    with no existing entry around (the actual real-world import use case:
    setting up a fresh instance)."""
    hass.states.async_set("fan.in1", "on", {"percentage": 50})
    hass.states.async_set("fan.ex1", "on", {"percentage": 10})

    # Produce the "exported" JSON by running the wizard once, without
    # persisting it as a config entry (so the domain's unique_id stays free
    # for the import flow below).
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "wizard"}
    )
    final = await _advance_through_wizard(hass, result["flow_id"])
    entry_data = final["data"]
    exported_text = json.dumps(entry_data)

    # Completing that flow actually registered a real entry (unique_id
    # taken) -- remove it so the import flow below isn't blocked by the
    # single-instance rule; that is not what this test is about.
    for existing in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_remove(existing.entry_id)

    result2 = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    import_form = await hass.config_entries.flow.async_configure(
        result2["flow_id"], {"next_step_id": "import_config"}
    )
    assert import_form["step_id"] == "import_config"
    imported = await hass.config_entries.flow.async_configure(
        import_form["flow_id"], {"config_json": exported_text}
    )
    assert imported["type"] is FlowResultType.CREATE_ENTRY
    assert imported["data"] == entry_data


async def test_import_rejects_invalid_json(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "import_config"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"config_json": "{not valid"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["config_json"] == "invalid_json"


async def test_import_rejects_invalid_config(hass: HomeAssistant) -> None:
    bad_config = json.dumps({
        "intake_fans": ["fan.in1"], "exhaust_fans": [],
        "intake_profiles": {
            "fan.in1": {"control_type": "steps", "airflow_map": [30, 0]},
        },
        "min_home_step": 1, "min_away_step": 0, "min_home_pct": 20,
        "min_away_pct": 0, "interval": 30, "pressure_tolerance": 15,
        "max_correction_seconds": 180,
    })
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "import_config"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"config_json": bad_config}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["config_json"] == "invalid_config"


async def test_read_only_fan_skips_rules_step(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "wizard"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"intake_fans": ["fan.auto"], "exhaust_fans": []}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"control_type": "percentage", "read_only": True}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"max_airflow": 200}
    )
    # read-only: jumps straight to "global" (no more fans, rules skipped)
    assert result["step_id"] == "global"


async def test_options_wizard_reconfigure_keeps_other_fan(hass: HomeAssistant) -> None:
    """Running the options wizard again should pre-fill existing values and
    let the user change just the global settings without starting over."""
    hass.states.async_set("fan.in1", "on", {"percentage": 50})
    hass.states.async_set("fan.ex1", "on", {"percentage": 10})

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "wizard"}
    )
    final = await _advance_through_wizard(hass, result["flow_id"])
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    options_result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        options_result["flow_id"], {"next_step_id": "wizard"}
    )
    assert result["step_id"] == "wizard"
    # Keep the same two fans, just reconfigure.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"intake_fans": ["fan.in1"], "exhaust_fans": ["fan.ex1"]}
    )
    assert result["step_id"] == "fan_type"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"control_type": "steps", "read_only": False}
    )
    assert result["step_id"] == "fan_template"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"airflow_map": "0,40,80", "day_max": 1}
    )
    assert result["step_id"] == "fan_rules"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"add_another": False}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"control_type": "percentage"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"max_airflow": 600}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"add_another": False}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "min_home_step": 1, "min_away_step": 0, "min_home_pct": 20,
            "min_away_pct": 0, "interval": 45, "pressure_tolerance": 20,
            "max_correction_seconds": 120, "boost_minutes": 10,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["interval"] == 45
    assert result["data"]["intake_profiles"]["fan.in1"]["airflow_map"] == [0, 40, 80]

async def test_fan_template_prefills_airflow_map(hass: HomeAssistant) -> None:
    """Picking a template sets the *default* shown on the next screen;
    it does not silently finalize the value -- the user still submits it."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "wizard"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"intake_fans": ["fan.in1"], "exhaust_fans": []}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"control_type": "steps"}
    )
    assert result["step_id"] == "fan_template"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"template": "tion_4s"}
    )
    assert result["step_id"] == "fan_performance"
    prefilled = result["data_schema"]({})["airflow_map"]
    assert prefilled == "0,30,45,60,75,90,140"
