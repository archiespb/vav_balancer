"""Pure math tests for balancer.py and models.py (no Home Assistant needed)."""
from __future__ import annotations

import pytest

from custom_components.vav_balancer.balancer import FanInput, balance
from custom_components.vav_balancer.models import FanModel, Rule, parse_airflow_map


def _step_model(entity_id: str, role: str, airflow_map=(0, 30, 45, 60, 75, 90, 140)) -> FanModel:
    return FanModel.from_profile(
        entity_id, role,
        {"control_type": "steps", "airflow_map": list(airflow_map)},
    )


def _pct_model(entity_id: str, role: str, max_airflow: float) -> FanModel:
    return FanModel.from_profile(
        entity_id, role, {"control_type": "percentage", "max_airflow": max_airflow},
    )


def _input(model: FanModel, demand: float, ceiling: float | None = None) -> FanInput:
    ceiling = model.max_level if ceiling is None else ceiling
    return FanInput(model, True, None, demand, ceiling, demand)


class TestParseAirflowMap:
    def test_valid(self) -> None:
        assert parse_airflow_map("0,30,45,60") == [0.0, 30.0, 45.0, 60.0]

    def test_too_short(self) -> None:
        with pytest.raises(ValueError):
            parse_airflow_map("0")

    def test_negative(self) -> None:
        with pytest.raises(ValueError):
            parse_airflow_map("0,-5,10")

    def test_not_nondecreasing(self) -> None:
        with pytest.raises(ValueError):
            parse_airflow_map("0,30,20")


class TestFanModel:
    def test_steps_flow(self) -> None:
        model = _step_model("fan.a", "intake")
        assert model.flow(0) == 0
        assert model.flow(6) == 140
        assert model.max_level == 6

    def test_percentage_flow(self) -> None:
        model = _pct_model("fan.a", "intake", 500)
        assert model.flow(50) == 250
        assert model.flow(100) == 500

    def test_level_for_flow_round_up(self) -> None:
        model = _step_model("fan.a", "intake")
        # smallest step whose flow >= 50 is step 3 (60); step 2 is 45 < 50
        assert model.level_for_flow(50, round_up=True) == 3

    def test_level_for_flow_round_down(self) -> None:
        model = _step_model("fan.a", "intake")
        # largest step whose flow <= 50 is step 2 (45); step 3 is 60 > 50
        assert model.level_for_flow(50, round_up=False) == 2

    def test_invalid_control_type(self) -> None:
        with pytest.raises(ValueError):
            FanModel.from_profile("fan.a", "intake", {"control_type": "bogus"})


class TestRule:
    def test_above_requires_threshold(self) -> None:
        with pytest.raises(ValueError):
            Rule.from_dict({"entity_id": "sensor.x", "mode": "above", "output": 1})

    def test_linear_requires_threshold_high(self) -> None:
        with pytest.raises(ValueError):
            Rule.from_dict(
                {"entity_id": "sensor.x", "mode": "linear", "threshold": 10, "output": 1}
            )

    def test_entity_threshold_bypasses_fixed_requirement(self) -> None:
        rule = Rule.from_dict(
            {
                "entity_id": "sensor.x", "mode": "above",
                "threshold_entity": "sensor.avg", "output": 1,
            }
        )
        assert rule.threshold is None
        assert rule.threshold_entity == "sensor.avg"


class TestBalance:
    def test_balanced_mixed_fans_converge_to_zero_delta(self) -> None:
        i1 = _input(_step_model("fan.in1", "intake"), 1)
        i2 = _input(_pct_model("fan.in2", "intake", 200), 10.0)
        e1 = _input(_step_model("fan.ex1", "exhaust"), 3)
        e2 = _input(_pct_model("fan.ex2", "exhaust", 100), 0.0)

        plan = balance([i1, i2], [e1, e2])
        assert plan.intake_flow == pytest.approx(plan.exhaust_flow, abs=0.5)
        assert "residual_positive_delta" not in plan.notes

    def test_intake_capped_throttles_exhaust(self) -> None:
        i1 = _input(_step_model("fan.in1", "intake"), 1, ceiling=2)
        e1 = _input(_step_model("fan.ex1", "exhaust"), 3)

        plan = balance([i1], [e1])
        assert "intake_capped" in plan.notes
        assert "exhaust_throttled_for_positive_pressure" in plan.notes
        # exhaust must never exceed what intake can actually supply
        assert plan.exhaust_flow <= plan.intake_flow + 0.5

    def test_unavailable_intake_excluded(self) -> None:
        model = _step_model("fan.in1", "intake")
        unavailable = FanInput(model, False, None, 3, model.max_level, 3)
        e1 = _input(_step_model("fan.ex1", "exhaust"), 1)

        plan = balance([unavailable], [e1])
        # the unavailable fan must not be commanded to any nonzero level
        assert plan.intake["fan.in1"].flow == 0

    def test_never_negative_pressure_in_target(self) -> None:
        """The computed TARGET should never have exhaust exceed intake."""
        i1 = _input(_step_model("fan.in1", "intake"), 0, ceiling=1)
        e1 = _input(_pct_model("fan.ex1", "exhaust", 550), 90)

        plan = balance([i1], [e1])
        assert plan.exhaust_flow <= plan.intake_flow + 0.5
