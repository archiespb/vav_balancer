"""Branch tests for VAVController that don't need real fan profiles.

The controller is built from an empty config entry, and fan models, rules
and the plan are injected as small fakes. That keeps each test focused on
one controller decision (pacing, ordering, watchdog, fallbacks...) instead
of on FanModel/balance() internals, which have their own tests.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import logging
import math
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_mock_service,
)

from custom_components.vav_balancer.balancer import FanInput
from custom_components.vav_balancer.const import (
    DOMAIN,
    PERCENT_SLEW,
    ROLE_EXHAUST,
    ROLE_INTAKE,
    STEP_SLEW,
)
from custom_components.vav_balancer.controller import VAVController

FLOW_PER_UNIT = 10.0  # m3/h per percent (or per step) for every fake fan


class FakeModel:
    """Minimal stand-in for FanModel: linear flow, simple clamp."""

    def __init__(
        self,
        entity_id: str,
        role: str,
        *,
        steps: int | None = None,
        read_only: bool = False,
        rules: list[Any] | None = None,
    ) -> None:
        self.entity_id = entity_id
        self.role = role
        self.is_steps = steps is not None
        self.max_level = float(steps) if steps is not None else 100.0
        self.read_only = read_only
        self.rules = rules or []
        self.night_max = None
        self.day_max = None

    def flow(self, level: float) -> float:
        return level * FLOW_PER_UNIT

    def level_for_flow(self, flow: float, round_up: bool = False) -> float:
        raw = flow / FLOW_PER_UNIT
        return float(math.ceil(raw) if round_up else math.floor(raw))

    def clamp(self, level: float, ceiling: float | None = None) -> float:
        top = self.max_level if ceiling is None else ceiling
        return max(0.0, min(top, level))


def target(model: FakeModel, level: float) -> SimpleNamespace:
    return SimpleNamespace(
        entity_id=model.entity_id, level=level, flow=model.flow(level)
    )


def make_plan(
    intake: list[tuple[FakeModel, float]] = (),
    exhaust: list[tuple[FakeModel, float]] = (),
) -> SimpleNamespace:
    return SimpleNamespace(
        intake={m.entity_id: target(m, lvl) for m, lvl in intake},
        exhaust={m.entity_id: target(m, lvl) for m, lvl in exhaust},
    )


def make_rule(**overrides: Any) -> SimpleNamespace:
    rule = SimpleNamespace(
        entity_id="sensor.humidity",
        threshold=60.0,
        threshold_entity=None,
        threshold_high=None,
        threshold_high_entity=None,
        on_unavailable="max",
        mode="above",
        delay_seconds=0.0,
        hysteresis=0.0,
        fallback=lambda: 99.0,
        evaluate=lambda state, threshold, threshold_high, was_active: (
            50.0 if float(state.state) > threshold else None
        ),
    )
    for key, value in overrides.items():
        setattr(rule, key, value)
    return rule


@pytest.fixture
def ctrl(hass: HomeAssistant) -> VAVController:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    controller = VAVController(hass, entry)
    controller._pressure_tolerance = 50.0
    controller._max_correction = 60
    return controller


def use_models(ctrl: VAVController, *models: FakeModel) -> None:
    ctrl._models = {m.entity_id: m for m in models}


def local_dt(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 1, 15, hour, minute, tzinfo=dt_util.get_default_time_zone())


# ----------------------------------------------------------------------
# Setup helpers
# ----------------------------------------------------------------------
def test_fan_without_profile_is_skipped(
    ctrl: VAVController, caplog: pytest.LogCaptureFixture
) -> None:
    assert ctrl._load_models(["fan.orphan"], {}, ROLE_INTAKE) == []
    assert "fan.orphan has no profile" in caplog.text


def test_tracked_entities_include_dynamic_thresholds(ctrl: VAVController) -> None:
    rule = make_rule(
        threshold_entity="sensor.avg_humidity",
        threshold_high_entity="sensor.avg_humidity_high",
    )
    use_models(ctrl, FakeModel("fan.bath", ROLE_EXHAUST, rules=[rule]))
    ctrl._presence = "binary_sensor.home"
    ctrl._night_start = "input_datetime.night_start"

    assert ctrl._tracked_entities() == sorted([
        "fan.bath", "sensor.humidity", "sensor.avg_humidity",
        "sensor.avg_humidity_high", "binary_sensor.home",
        "input_datetime.night_start",
    ])


async def test_set_enabled_recalculates_only_on_change(ctrl: VAVController) -> None:
    with patch.object(ctrl, "_recalculate") as recalc:
        ctrl.set_enabled(True)  # already enabled -> no-op
        recalc.assert_not_called()
        ctrl.set_enabled(False)
        recalc.assert_called_once_with("enabled toggled")
    assert ctrl.enabled is False


async def test_tick_recalculates_then_executes(ctrl: VAVController) -> None:
    with (
        patch.object(ctrl, "_recalculate") as recalc,
        patch.object(ctrl, "_async_execute") as execute,
    ):
        await ctrl._handle_tick(dt_util.utcnow())
    recalc.assert_called_once_with("execution tick")
    execute.assert_awaited_once()


async def test_state_change_event_triggers_recalculation(
    ctrl: VAVController,
) -> None:
    event = SimpleNamespace(
        data={"entity_id": "sensor.humidity", "new_state": None}
    )
    with patch.object(ctrl, "_recalculate") as recalc:
        ctrl._handle_state_event(event)
    recalc.assert_called_once_with("state change: sensor.humidity")


async def test_failed_calculation_keeps_previous_plan(
    ctrl: VAVController, caplog: pytest.LogCaptureFixture
) -> None:
    previous = make_plan()
    ctrl.plan = previous
    updates: list[None] = []
    ctrl.async_add_listener(lambda: updates.append(None))

    with patch(
        "custom_components.vav_balancer.controller.balance",
        side_effect=ZeroDivisionError,
    ):
        ctrl._recalculate("test")

    assert ctrl.plan is previous
    assert "calculation failed, keeping previous plan" in caplog.text
    assert len(updates) == 1  # entities still get notified


# ----------------------------------------------------------------------
# Thresholds from other sensors
# ----------------------------------------------------------------------
async def test_unavailable_threshold_sensor_uses_fallback(
    hass: HomeAssistant, ctrl: VAVController, caplog: pytest.LogCaptureFixture
) -> None:
    hass.states.async_set("sensor.humidity", "70")
    hass.states.async_set("sensor.avg_humidity", "unavailable")
    rule = make_rule(threshold_entity="sensor.avg_humidity")

    level, debug = ctrl._evaluate_rule(rule, "fan.bath:0")

    assert level == 99.0
    assert debug["threshold"] is None
    assert debug["threshold_source"] == "sensor.avg_humidity"
    assert "Threshold sensor sensor.avg_humidity is unavailable" in caplog.text


async def test_non_numeric_threshold_then_recovery(
    hass: HomeAssistant, ctrl: VAVController, caplog: pytest.LogCaptureFixture
) -> None:
    hass.states.async_set("sensor.humidity", "70")
    hass.states.async_set("sensor.avg_humidity", "n/a")
    rule = make_rule(threshold_entity="sensor.avg_humidity")

    level, _ = ctrl._evaluate_rule(rule, "fan.bath:0")
    assert level == 99.0
    assert "non-numeric state 'n/a'" in caplog.text

    caplog.clear()
    hass.states.async_set("sensor.avg_humidity", "65")
    level, debug = ctrl._evaluate_rule(rule, "fan.bath:0")
    assert level == 50.0  # 70 > 65 -> normal evaluation again
    assert debug["threshold"] == 65.0
    assert "is available again" in caplog.text


# ----------------------------------------------------------------------
# Read-only fans
# ----------------------------------------------------------------------
async def test_read_only_fan_pins_its_actual_level(
    hass: HomeAssistant, ctrl: VAVController
) -> None:
    model = FakeModel("fan.kitchen", ROLE_EXHAUST, read_only=True)
    hass.states.async_set("fan.kitchen", "on", {"percentage": 40})

    assert ctrl._build_input(model) == FanInput(model, True, None, 40.0, 40.0, 40.0)
    assert ctrl.rule_debug_for("fan.kitchen") == []


async def test_read_only_fan_unavailable(
    hass: HomeAssistant, ctrl: VAVController
) -> None:
    model = FakeModel("fan.kitchen", ROLE_EXHAUST, read_only=True)
    hass.states.async_set("fan.kitchen", "unavailable")

    assert ctrl._build_input(model) == FanInput(model, False, None, 0.0, 0.0, 0.0)


async def test_read_only_fan_unreadable_counts_as_zero(
    hass: HomeAssistant, ctrl: VAVController, caplog: pytest.LogCaptureFixture
) -> None:
    model = FakeModel("fan.kitchen", ROLE_EXHAUST, read_only=True)
    hass.states.async_set("fan.kitchen", "on")  # no percentage attribute

    assert ctrl._build_input(model) == FanInput(model, True, None, 0.0, 0.0, 0.0)
    assert "treated as 0 for balancing" in caplog.text


# ----------------------------------------------------------------------
# Presence and night window
# ----------------------------------------------------------------------
async def test_presence(
    hass: HomeAssistant, ctrl: VAVController, caplog: pytest.LogCaptureFixture
) -> None:
    ctrl._presence = "binary_sensor.home"

    hass.states.async_set("binary_sensor.home", "unavailable")
    assert ctrl._is_home() is True
    assert "assuming HOME" in caplog.text

    hass.states.async_set("binary_sensor.home", "off")
    assert ctrl._is_home() is False
    assert "is available again" in caplog.text

    hass.states.async_set("binary_sensor.home", "on")
    assert ctrl._is_home() is True


@pytest.mark.parametrize(
    ("start", "end", "now_hour", "expected"),
    [
        ((23, 0), (7, 0), 23, True),   # window across midnight, evening
        ((23, 0), (7, 0), 3, True),    # window across midnight, early morning
        ((23, 0), (7, 0), 12, False),
        ((1, 0), (5, 0), 2, True),     # same-day window
        ((1, 0), (5, 0), 5, False),    # end is exclusive
        ((1, 0), (1, 0), 1, False),    # start == end -> never night
    ],
)
async def test_night_window(
    hass: HomeAssistant, ctrl: VAVController, freezer,
    start: tuple[int, int], end: tuple[int, int], now_hour: int, expected: bool,
) -> None:
    hass.states.async_set(
        "input_datetime.night_start", "x", {"hour": start[0], "minute": start[1]}
    )
    hass.states.async_set(
        "input_datetime.night_end", "x", {"hour": end[0], "minute": end[1]}
    )
    ctrl._night_start = "input_datetime.night_start"
    ctrl._night_end = "input_datetime.night_end"
    freezer.move_to(local_dt(now_hour))

    assert ctrl._is_night() is expected


async def test_night_time_parsed_from_state_string(
    hass: HomeAssistant, ctrl: VAVController
) -> None:
    hass.states.async_set("sensor.start", "2026-01-15 22:30:00")
    hass.states.async_set("sensor.bad", "x", {"hour": "xx", "minute": 0})
    hass.states.async_set("sensor.gone", "unavailable")

    assert ctrl._entity_time("sensor.start") == local_dt(22, 30).time()
    assert ctrl._entity_time("sensor.bad") is None
    assert ctrl._entity_time("sensor.gone") is None
    assert ctrl._entity_time(None) is None


# ----------------------------------------------------------------------
# Slew limiter
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    ("current", "goal", "expected"),
    [(1, 3, 1 + STEP_SLEW), (3, 1, 3 - STEP_SLEW), (2, 2, 2)],
)
def test_slew_steps(current: float, goal: float, expected: float) -> None:
    model = FakeModel("fan.x", ROLE_INTAKE, steps=3)
    assert VAVController._slew(model, current, goal) == expected


@pytest.mark.parametrize(
    ("current", "goal", "expected"),
    [
        (20, 80, 20 + PERCENT_SLEW),
        (80, 20, 80 - PERCENT_SLEW),
        (20, 20 + PERCENT_SLEW - 1, 20 + PERCENT_SLEW - 1),  # small step: exact
        (99, 150, 100),  # never above 100
    ],
)
def test_slew_percent(current: float, goal: float, expected: float) -> None:
    model = FakeModel("fan.x", ROLE_INTAKE)
    assert VAVController._slew(model, current, goal) == expected


# ----------------------------------------------------------------------
# Execution tick
# ----------------------------------------------------------------------
async def test_disabled_or_unplanned_controller_sends_nothing(
    hass: HomeAssistant, ctrl: VAVController
) -> None:
    calls = async_mock_service(hass, "fan", "set_percentage")
    fan_in = FakeModel("fan.in", ROLE_INTAKE)
    use_models(ctrl, fan_in)
    hass.states.async_set("fan.in", "on", {"percentage": 20})

    await ctrl._async_execute()  # plan is None
    ctrl.plan = make_plan(intake=[(fan_in, 60)])
    ctrl.enabled = False
    await ctrl._async_execute()

    assert calls == []
    assert ctrl.last_execution is None


async def test_converged_fans_get_no_commands(
    hass: HomeAssistant, ctrl: VAVController
) -> None:
    """Exhaust inside the band keeps its own target (no pacing scale)."""
    calls = async_mock_service(hass, "fan", "set_percentage")
    fan_in = FakeModel("fan.in", ROLE_INTAKE)
    fan_out = FakeModel("fan.out", ROLE_EXHAUST)
    use_models(ctrl, fan_in, fan_out)
    hass.states.async_set("fan.in", "on", {"percentage": 40})
    hass.states.async_set("fan.out", "on", {"percentage": 40})
    ctrl.actual_intake = ctrl.actual_exhaust = 400.0
    ctrl.plan = make_plan(intake=[(fan_in, 40)], exhaust=[(fan_out, 40)])
    updates: list[None] = []
    ctrl.async_add_listener(lambda: updates.append(None))

    await ctrl._async_execute()

    assert calls == []
    assert len(updates) == 1  # entities are still refreshed
    assert ctrl.last_commands == []
    assert ctrl.last_execution is not None


async def test_ramp_up_paces_exhaust_and_raises_intake_first(
    hass: HomeAssistant, ctrl: VAVController
) -> None:
    calls = async_mock_service(hass, "fan", "set_percentage")
    fan_in = FakeModel("fan.in", ROLE_INTAKE)
    fan_out = FakeModel("fan.out", ROLE_EXHAUST)
    use_models(ctrl, fan_in, fan_out)
    hass.states.async_set("fan.in", "on", {"percentage": 30})
    hass.states.async_set("fan.out", "on", {"percentage": 30})
    ctrl.actual_intake = ctrl.actual_exhaust = 300.0
    # Both sides want 500 m3/h; exhaust may only go to intake + 50 = 350 now.
    ctrl.plan = make_plan(intake=[(fan_in, 50)], exhaust=[(fan_out, 50)])

    await ctrl._async_execute()

    assert [(c.data["entity_id"], c.data["percentage"]) for c in calls] == [
        ("fan.in", 30 + PERCENT_SLEW),  # safe direction goes first
        ("fan.out", 30 + PERCENT_SLEW),
    ]
    assert all(cmd["to"] <= 35 for cmd in ctrl.last_commands)


async def test_urgent_imbalance_jumps_exhaust_to_paced_goal(
    hass: HomeAssistant, ctrl: VAVController
) -> None:
    calls = async_mock_service(hass, "fan", "set_percentage")
    fan_in = FakeModel("fan.in", ROLE_INTAKE)
    fan_out = FakeModel("fan.out", ROLE_EXHAUST)
    use_models(ctrl, fan_in, fan_out)
    hass.states.async_set("fan.in", "on", {"percentage": 30})
    hass.states.async_set("fan.out", "on", {"percentage": 50})
    ctrl.actual_intake, ctrl.actual_exhaust = 300.0, 500.0  # -200, far beyond 50
    ctrl.plan = make_plan(intake=[(fan_in, 30)], exhaust=[(fan_out, 50)])

    await ctrl._async_execute()

    # No gradual -5% ramp: straight down into the band around actual intake.
    assert [(c.data["entity_id"], c.data["percentage"]) for c in calls] == [
        ("fan.out", 35),
    ]


async def test_read_only_and_unreadable_fans_are_never_commanded(
    hass: HomeAssistant, ctrl: VAVController
) -> None:
    calls = async_mock_service(hass, "fan", "set_percentage")
    ro = FakeModel("fan.ro", ROLE_INTAKE, read_only=True)
    blind = FakeModel("fan.blind", ROLE_INTAKE, steps=3)
    use_models(ctrl, ro, blind)
    hass.states.async_set("fan.ro", "on", {"percentage": 10})
    hass.states.async_set("fan.blind", "on")  # no presets, no percentage
    ctrl.plan = make_plan(intake=[(ro, 80), (blind, 3)])

    await ctrl._async_execute()

    assert calls == []


async def test_step_fan_without_presets_uses_percentage(
    hass: HomeAssistant, ctrl: VAVController
) -> None:
    calls = async_mock_service(hass, "fan", "set_percentage")
    fan = FakeModel("fan.steps", ROLE_INTAKE, steps=3)
    use_models(ctrl, fan)
    hass.states.async_set("fan.steps", "on", {"percentage": 33})  # step 1
    ctrl.plan = make_plan(intake=[(fan, 3)])

    await ctrl._async_execute()

    assert [c.data["percentage"] for c in calls] == [67]  # step 2 of 3


async def test_reaching_zero_turns_fan_off(
    hass: HomeAssistant, ctrl: VAVController
) -> None:
    calls = async_mock_service(hass, "fan", "turn_off")
    fan = FakeModel("fan.in", ROLE_INTAKE)
    use_models(ctrl, fan)
    hass.states.async_set("fan.in", "on", {"percentage": 3})
    ctrl.plan = make_plan(intake=[(fan, 0)])

    await ctrl._async_execute()

    assert [c.data for c in calls] == [{"entity_id": "fan.in"}]


async def test_failed_command_is_logged_and_not_recorded(
    hass: HomeAssistant, ctrl: VAVController, caplog: pytest.LogCaptureFixture
) -> None:
    async def _fail(call: ServiceCall) -> None:
        raise HomeAssistantError("device offline")

    hass.services.async_register("fan", "set_percentage", _fail)
    fan = FakeModel("fan.in", ROLE_INTAKE)
    use_models(ctrl, fan)
    hass.states.async_set("fan.in", "on", {"percentage": 20})
    ctrl.plan = make_plan(intake=[(fan, 60)])

    await ctrl._async_execute()

    assert ctrl.last_commands == []
    assert "command for fan.in failed: device offline" in caplog.text


async def test_stuck_fan_watchdog_forces_full_correction(
    hass: HomeAssistant, ctrl: VAVController, caplog: pytest.LogCaptureFixture
) -> None:
    """The mocked service never changes the state, so the fan looks stuck.

    Elapsed stall time is simulated by moving the recorded stall start back
    instead of moving the clock, so the test doesn't depend on how (or
    whether) time is frozen elsewhere in the test session.
    """
    calls = async_mock_service(hass, "fan", "set_percentage")
    fan = FakeModel("fan.in", ROLE_INTAKE)
    use_models(ctrl, fan)
    hass.states.async_set("fan.in", "on", {"percentage": 20})
    ctrl.plan = make_plan(intake=[(fan, 60)])

    def age_stall(seconds: float) -> None:
        ctrl._deviation_since["fan.in"] -= timedelta(seconds=seconds)

    await ctrl._async_execute()  # 1st tick: nothing to compare with yet
    assert ctrl.stuck_seconds_for("fan.in") is None

    await ctrl._async_execute()  # 2nd tick: no movement -> stall timer starts
    age_stall(30)
    assert ctrl.stuck_seconds_for("fan.in") == pytest.approx(30, abs=1)

    await ctrl._async_execute()  # still below max_correction -> normal slew
    age_stall(ctrl._max_correction)
    caplog.set_level(logging.WARNING)
    await ctrl._async_execute()  # stalled long enough -> jump straight to goal

    assert [c.data["percentage"] for c in calls] == [25, 25, 25, 60]
    assert "forcing full correction" in caplog.text

    hass.states.async_set("fan.in", "on", {"percentage": 60})
    await ctrl._async_execute()  # converged -> timer cleared
    assert ctrl.stuck_seconds_for("fan.in") is None


async def test_actual_flow_for(hass: HomeAssistant, ctrl: VAVController) -> None:
    use_models(ctrl, FakeModel("fan.in", ROLE_INTAKE))

    hass.states.async_set("fan.in", "off", {"percentage": 40})
    assert ctrl.actual_flow_for("fan.in") == 0.0  # "off" wins over attributes

    hass.states.async_set("fan.in", "on", {"percentage": 40})
    assert ctrl.actual_flow_for("fan.in") == 40 * FLOW_PER_UNIT

    assert ctrl.actual_flow_for("fan.nope") is None
