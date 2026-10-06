"""Data models (fan profiles and sensor rules) for the VAV balancer."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import State

from .const import (
    CONTROL_PERCENTAGE,
    CONTROL_STEPS,
    MODE_ABOVE,
    MODE_BELOW,
    MODE_LINEAR,
    MODE_OFF,
    MODE_ON,
    PERCENT_SLEW,
    PROF_AIRFLOW_MAP,
    PROF_CONTROL_TYPE,
    PROF_DAY_MAX,
    PROF_MAX_AIRFLOW,
    PROF_NIGHT_MAX,
    PROF_READ_ONLY,
    PROF_RULES,
    RULE_DELAY,
    RULE_ENTITY,
    RULE_HYSTERESIS,
    RULE_MODE,
    RULE_ON_UNAVAILABLE,
    RULE_OUTPUT,
    RULE_OUTPUT_LOW,
    RULE_THRESHOLD,
    RULE_THRESHOLD_ENTITY,
    RULE_THRESHOLD_HIGH,
    RULE_THRESHOLD_HIGH_ENTITY,
    RULE_MODES,
    STEP_SLEW,
    UNAVAILABLE_ACTIVE,
    UNAVAILABLE_INACTIVE,
)

_LOGGER = logging.getLogger(__name__)

_ON_VALUES = {"on", "true", "home", "open", "detected", "yes", "1"}
_OFF_VALUES = {"off", "false", "closed", "not_home", "clear", "no", "0"}


def parse_airflow_map(raw: Any) -> list[float]:
    """Parse "0,30,45,60" (or a list) into a validated airflow map.

    Raises ValueError when the map is unusable.
    """
    if isinstance(raw, (list, tuple)):
        values = [float(v) for v in raw]
    else:
        tokens = [t.strip() for t in str(raw).split(",") if t.strip() != ""]
        values = [float(t) for t in tokens]

    if len(values) < 2:
        raise ValueError("airflow map needs at least two entries (step 0 and step 1)")
    if any(v < 0 for v in values):
        raise ValueError("airflow values must not be negative")
    if any(b < a for a, b in zip(values, values[1:])):
        raise ValueError("airflow map must be non-decreasing")
    return values


@dataclass(frozen=True)
class Rule:
    """A single sensor -> demanded level rule.

    The threshold and threshold_high bounds can each be either a fixed
    number (``threshold`` / ``threshold_high``) or the live state of
    another numeric entity (``threshold_entity`` / ``threshold_high_entity``,
    e.g. a virtual "average humidity" sensor). When an entity is set it
    always takes precedence over the fixed number for that bound; the
    caller (the controller) resolves it from hass.states and passes the
    resolved number into :meth:`evaluate`.
    """

    entity_id: str
    mode: str
    output: float
    threshold: float | None = None
    threshold_high: float | None = None
    output_low: float | None = None
    on_unavailable: str = UNAVAILABLE_INACTIVE
    threshold_entity: str | None = None
    threshold_high_entity: str | None = None
    delay_seconds: float = 0.0
    hysteresis: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Rule":
        """Build a rule from stored configuration, validating it."""
        mode = data[RULE_MODE]
        if mode not in RULE_MODES:
            raise ValueError(f"unknown rule mode '{mode}'")

        def _opt(key: str) -> float | None:
            value = data.get(key)
            return None if value is None else float(value)

        threshold_entity = data.get(RULE_THRESHOLD_ENTITY) or None
        threshold_high_entity = data.get(RULE_THRESHOLD_HIGH_ENTITY) or None

        rule = cls(
            entity_id=data[RULE_ENTITY],
            mode=mode,
            output=float(data[RULE_OUTPUT]),
            threshold=_opt(RULE_THRESHOLD),
            threshold_high=_opt(RULE_THRESHOLD_HIGH),
            output_low=_opt(RULE_OUTPUT_LOW),
            on_unavailable=data.get(RULE_ON_UNAVAILABLE, UNAVAILABLE_INACTIVE),
            threshold_entity=threshold_entity,
            threshold_high_entity=threshold_high_entity,
            delay_seconds=max(0.0, float(data.get(RULE_DELAY, 0.0))),
            hysteresis=max(0.0, float(data.get(RULE_HYSTERESIS, 0.0))),
        )
        needs_threshold = mode in (MODE_ABOVE, MODE_BELOW, MODE_LINEAR)
        if needs_threshold and rule.threshold is None and not rule.threshold_entity:
            raise ValueError(f"rule for {rule.entity_id}: threshold is required")
        if mode == MODE_LINEAR:
            if rule.threshold_high is None and not rule.threshold_high_entity:
                raise ValueError(
                    f"rule for {rule.entity_id}: threshold_high is required for linear mode"
                )
            # Only enforce threshold_high > threshold when both bounds are
            # fixed numbers; a dynamic bound can only be checked at runtime.
            if (
                rule.threshold is not None
                and rule.threshold_high is not None
                and not rule.threshold_entity
                and not rule.threshold_high_entity
                and rule.threshold_high <= rule.threshold
            ):
                raise ValueError(f"rule for {rule.entity_id}: invalid linear range")
        return rule

    def fallback(self) -> float | None:
        """Result used when the rule cannot be meaningfully evaluated
        (its own sensor, or a dynamic threshold entity, is unavailable).
        """
        return self.output if self.on_unavailable == UNAVAILABLE_ACTIVE else None

    def evaluate(
        self,
        state: State | None,
        *,
        threshold: float | None,
        threshold_high: float | None,
        was_active: bool = False,
    ) -> float | None:
        """Return the demanded level, or None when the rule is inactive.

        ``threshold`` / ``threshold_high`` are the already-resolved numeric
        bounds for this evaluation (fixed value or a dynamic entity's
        current state, resolved by the caller). ``was_active`` is whether
        this rule was active on the previous evaluation; together with
        ``hysteresis`` it prevents chattering when the raw value hovers
        right at the threshold: activation still happens exactly at
        ``threshold``, but an already-active rule only deactivates once the
        value has moved ``hysteresis`` past the threshold in the "off"
        direction, not the instant it recrosses it.
        """
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return self.fallback()

        raw = str(state.state).lower()

        if self.mode == MODE_ON:
            return self.output if raw in _ON_VALUES else None
        if self.mode == MODE_OFF:
            return self.output if raw in _OFF_VALUES else None

        try:
            value = float(state.state)
        except (TypeError, ValueError):
            return self.fallback()

        if threshold is None:
            # A dynamic threshold entity could not be resolved; the caller
            # should normally use fallback() directly, but stay safe here.
            return self.fallback()

        if self.mode == MODE_ABOVE:
            off_point = threshold - self.hysteresis if was_active else threshold
            return self.output if value > off_point else None
        if self.mode == MODE_BELOW:
            off_point = threshold + self.hysteresis if was_active else threshold
            return self.output if value < off_point else None

        # MODE_LINEAR
        off_point = threshold - self.hysteresis if was_active else threshold
        if value < off_point:
            return None
        if threshold_high is None:
            return self.fallback()
        span = threshold_high - threshold
        # Clamped to [0, 1]: while holding in the hysteresis band (value
        # below the nominal threshold but above off_point), the ramp would
        # otherwise go negative -- pin it at output_low instead.
        ratio = 1.0 if span <= 0 else max(0.0, min(1.0, (value - threshold) / span))
        low = self.output_low if self.output_low is not None else 0.0
        return low + ratio * (self.output - low)


@dataclass(frozen=True)
class FanModel:
    """Static description of one controlled fan."""

    entity_id: str
    role: str
    control: str
    airflow_map: tuple[float, ...]
    max_airflow: float
    night_max: float | None
    rules: tuple[Rule, ...]
    read_only: bool = False
    day_max: float | None = None

    # -- construction ------------------------------------------------------
    @classmethod
    def from_profile(cls, entity_id: str, role: str, profile: dict[str, Any]) -> "FanModel":
        """Validate a stored profile and convert it to a FanModel."""
        control = profile[PROF_CONTROL_TYPE]
        if control not in (CONTROL_STEPS, CONTROL_PERCENTAGE):
            raise ValueError(f"unknown control type '{control}'")

        airflow_map: tuple[float, ...] = ()
        max_airflow = 0.0
        if control == CONTROL_STEPS:
            airflow_map = tuple(parse_airflow_map(profile[PROF_AIRFLOW_MAP]))
        else:
            max_airflow = float(profile[PROF_MAX_AIRFLOW])
            if max_airflow <= 0:
                raise ValueError("max airflow must be positive")

        rules: list[Rule] = []
        for raw_rule in profile.get(PROF_RULES, []) or []:
            try:
                rules.append(Rule.from_dict(raw_rule))
            except (KeyError, TypeError, ValueError) as err:
                _LOGGER.warning(
                    "Config validation: ignoring invalid rule of %s (%s): %s",
                    entity_id, raw_rule, err,
                )

        night_raw = profile.get(PROF_NIGHT_MAX)
        model = cls(
            entity_id=entity_id,
            role=role,
            control=control,
            airflow_map=airflow_map,
            max_airflow=max_airflow,
            night_max=None,
            rules=tuple(rules),
            read_only=bool(profile.get(PROF_READ_ONLY, False)),
        )
        if night_raw is not None:
            object.__setattr__(model, "night_max", model.clamp(float(night_raw)))
        day_raw = profile.get(PROF_DAY_MAX)
        if day_raw is not None:
            object.__setattr__(model, "day_max", model.clamp(float(day_raw)))
        return model

    # -- helpers -----------------------------------------------------------
    @property
    def is_steps(self) -> bool:
        return self.control == CONTROL_STEPS

    @property
    def max_level(self) -> float:
        """Highest level: last step index, or 100 %."""
        return float(len(self.airflow_map) - 1) if self.is_steps else 100.0

    @property
    def increment(self) -> float:
        """Level increment used by iterative ramping."""
        return float(STEP_SLEW) if self.is_steps else PERCENT_SLEW

    def clamp(self, level: float, ceiling: float | None = None) -> float:
        """Clamp a level to [0, ceiling] (rounded to int for step fans)."""
        upper = self.max_level if ceiling is None else min(ceiling, self.max_level)
        value = min(max(float(level), 0.0), upper)
        return float(round(value)) if self.is_steps else value

    def flow(self, level: float) -> float:
        """Airflow (m3/h) produced at the given level."""
        if self.is_steps:
            idx = int(round(min(max(level, 0.0), self.max_level)))
            return self.airflow_map[idx]
        return min(max(level, 0.0), 100.0) / 100.0 * self.max_airflow

    def level_for_flow(self, flow: float, *, round_up: bool = True) -> float:
        """Inverse of flow(): the level that produces about this airflow.

        For a percentage fan this is exact. For a step fan, ``round_up``
        (the default) picks the smallest step whose airflow is >= ``flow``,
        so the result never undershoots the requested value; pass
        ``round_up=False`` for the largest step that does not exceed it.
        """
        if flow <= 0:
            return 0.0
        if not self.is_steps:
            if self.max_airflow <= 0:
                return 0.0
            return min(100.0, max(0.0, flow / self.max_airflow * 100.0))
        if round_up:
            for idx, f in enumerate(self.airflow_map):
                if f >= flow - 1e-9:
                    return float(idx)
            return self.max_level
        best = 0
        for idx, f in enumerate(self.airflow_map):
            if f <= flow + 1e-9:
                best = idx
        return float(best)

    def level_per_flow(self) -> float:
        """Percent of level per m3/h (percentage fans only)."""
        return 100.0 / self.max_airflow
