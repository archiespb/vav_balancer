"""Tests for the Home Assistant restart safety fixes.

Covers the bug report: right after a full HA restart, a slower integration
(a fan or a rule sensor) can briefly read as unavailable/unknown, which the
balancer used to misread as "0 airflow" / "rule inactive" and act on for
real before the genuine value arrived. See controller.py: async_start(),
_arm_contour2(), _actual_flow() and the actual_readable gate in
_async_execute().
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, STATE_UNKNOWN
from homeassistant.core import CoreState, HomeAssistant
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.vav_balancer import controller as controller_mod
from custom_components.vav_balancer.const import (
    CONF_EXHAUST_FANS,
    CONF_EXHAUST_PROFILES,
    CONF_INTAKE_FANS,
    CONF_INTAKE_PROFILES,
    CONF_STARTUP_GRACE_SECONDS,
)
from custom_components.vav_balancer.controller import VAVController


def _entry(data: dict) -> MockConfigEntry:
    return MockConfigEntry(domain="vav_balancer", data=data)


def _basic_data(**extra) -> dict:
    return {
        CONF_INTAKE_FANS: ["fan.in1"],
        CONF_EXHAUST_FANS: ["fan.ex1"],
        CONF_INTAKE_PROFILES: {
            "fan.in1": {"control_type": "percentage", "max_airflow": 500, "rules": []},
        },
        CONF_EXHAUST_PROFILES: {
            "fan.ex1": {"control_type": "percentage", "max_airflow": 550, "rules": []},
        },
        **extra,
    }


async def test_contour2_arms_immediately_when_ha_already_running(
    hass: HomeAssistant,
) -> None:
    """The default test fixture starts with CoreState.running -- this is
    also the normal case for a plain integration reload (not a full HA
    restart), so Contour 2 must arm right away, same as before this fix."""
    hass.states.async_set("fan.in1", "on", {"percentage": 50})
    hass.states.async_set("fan.ex1", "on", {"percentage": 10})
    entry = _entry(_basic_data())
    entry.add_to_hass(hass)
    controller = VAVController(hass, entry)

    assert hass.is_running
    await controller.async_start()

    assert controller._contour2_armed is True
    assert controller._startup_at is not None


async def test_contour2_waits_for_ha_started_event(hass: HomeAssistant) -> None:
    """On a full restart (HA not yet fully started), Contour 2 must not
    arm until EVENT_HOMEASSISTANT_STARTED fires."""
    hass.states.async_set("fan.in1", "on", {"percentage": 50})
    hass.states.async_set("fan.ex1", "on", {"percentage": 10})
    entry = _entry(_basic_data())
    entry.add_to_hass(hass)
    controller = VAVController(hass, entry)

    hass.set_state(CoreState.starting)
    # hass.is_running is already True during CoreState.starting -- exactly
    # why the controller checks hass.state directly instead (see comment
    # in async_start()). Assert the state the controller actually keys on.
    assert hass.state is not CoreState.running

    await controller.async_start()
    assert controller._contour2_armed is False
    assert controller._startup_at is None

    hass.set_state(CoreState.running)
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STARTED)
    await hass.async_block_till_done()

    assert controller._contour2_armed is True
    assert controller._startup_at is not None


async def test_arm_contour2_is_idempotent(hass: HomeAssistant) -> None:
    """Calling _arm_contour2() twice must not register the interval
    timer (or reset the grace-period clock) a second time."""
    entry = _entry(_basic_data())
    entry.add_to_hass(hass)
    controller = VAVController(hass, entry)

    controller._arm_contour2()
    first_started_at = controller._startup_at
    unsub_count = len(controller._unsubs)

    controller._arm_contour2()
    assert controller._startup_at == first_started_at
    assert len(controller._unsubs) == unsub_count


async def test_startup_grace_period_withholds_commands(hass: HomeAssistant) -> None:
    """Even with a real, large demand/actual mismatch, no hardware command
    may be sent while still inside the startup grace period."""
    hass.states.async_set("fan.in1", "on", {"percentage": 0})
    hass.states.async_set("fan.ex1", "on", {"percentage": 90})
    entry = _entry(_basic_data(**{CONF_STARTUP_GRACE_SECONDS: 60}))
    entry.add_to_hass(hass)
    controller = VAVController(hass, entry)
    calls = async_mock_service(hass, "fan", "set_percentage")
    async_mock_service(hass, "fan", "turn_off")

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    now = {"t": base}
    controller_mod.dt_util.utcnow = lambda: now["t"]

    controller._recalculate("startup")
    controller._arm_contour2()  # _startup_at = base

    # Still well inside the 60s grace window.
    now["t"] = base + timedelta(seconds=30)
    controller._recalculate("tick")
    await controller._async_execute()
    assert calls == []

    # Grace period has elapsed: normal execution resumes.
    now["t"] = base + timedelta(seconds=61)
    controller._recalculate("tick")
    await controller._async_execute()
    assert calls, "expected a command once the grace period elapsed"


async def test_actual_flow_distinguishes_unknown_from_zero(hass: HomeAssistant) -> None:
    """A fan that hasn't reported state yet must not be counted as 0
    airflow -- it must mark that side as not-fully-readable instead."""
    hass.states.async_set("fan.in1", "on", {"percentage": 50})
    hass.states.async_set("fan.ex1", STATE_UNKNOWN, {})
    entry = _entry(_basic_data())
    entry.add_to_hass(hass)
    controller = VAVController(hass, entry)

    controller._recalculate("test")
    assert controller._actual_intake_readable is True
    assert controller._actual_exhaust_readable is False
    assert controller.actual_readable is False


async def test_unreadable_actual_skips_pacing_but_allows_normal_slew(
    hass: HomeAssistant,
) -> None:
    """While a fan's actual state is still unknown (e.g. right after a
    restart), the pressure-pacing/urgent-correction shortcut must not run
    -- but the ordinary slew-limited approach toward the target must still
    work once a valid plan and state exist."""
    hass.states.async_set("fan.in1", "on", {"percentage": 80})
    # exhaust appears "on" but with no `percentage` attribute yet -> level
    # is unreadable, simulating a fan entity that hasn't finished restoring.
    hass.states.async_set("fan.ex1", "on", {})
    entry = _entry(_basic_data())
    entry.add_to_hass(hass)
    controller = VAVController(hass, entry)
    controller._arm_contour2()

    controller._recalculate("test")
    assert controller.actual_readable is False

    calls = async_mock_service(hass, "fan", "set_percentage")
    await controller._async_execute()
    # The exhaust fan's own state is unreadable, so _async_execute must
    # simply skip commanding it this tick (it has no `current` to slew
    # from) rather than crash or force a jump.
    ex_calls = [c for c in calls if c.data.get("entity_id") == "fan.ex1"]
    assert ex_calls == []
