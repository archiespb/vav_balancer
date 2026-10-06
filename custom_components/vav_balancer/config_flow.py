"""Dynamic multi-step config flow and options flow for the VAV balancer.

Wizard:
  1. discovery      - pick intake and exhaust fans
  2. per fan (loop) - fan_type -> fan_performance -> fan_rules (repeatable)
  3. global         - presence, occupancy minimums, night window, interval

The same wizard logic serves the initial setup and the OptionsFlow.
"""
from __future__ import annotations

import json
import logging
from copy import deepcopy
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
)

from .const import (
    CONF_EXHAUST_FANS,
    CONF_EXHAUST_PROFILES,
    CONF_INTAKE_FANS,
    CONF_INTAKE_PROFILES,
    CONF_INTERVAL,
    CONF_MIN_AWAY_PCT,
    CONF_MIN_AWAY_STEP,
    CONF_MIN_HOME_PCT,
    CONF_MIN_HOME_STEP,
    CONF_NIGHT_END,
    CONF_NIGHT_START,
    CONF_PRESENCE,
    CONF_MAX_CORRECTION_SECONDS,
    CONF_PRESSURE_TOLERANCE,
    CONTROL_PERCENTAGE,
    CONTROL_STEPS,
    DEFAULT_INTERVAL,
    DEFAULT_MAX_CORRECTION_SECONDS,
    DEFAULT_MIN_AWAY_PCT,
    DEFAULT_MIN_AWAY_STEP,
    DEFAULT_MIN_HOME_PCT,
    DEFAULT_MIN_HOME_STEP,
    DEFAULT_NAME,
    DEFAULT_PRESSURE_TOLERANCE,
    DOMAIN,
    FORM_ADD_ANOTHER,
    FORM_RESET_RULES,
    MAX_CORRECTION_SECONDS_LIMIT,
    MAX_INTERVAL,
    MAX_PRESSURE_TOLERANCE,
    MAX_RULE_DELAY,
    MAX_RULE_HYSTERESIS,
    MIN_CORRECTION_SECONDS,
    MIN_INTERVAL,
    MIN_PRESSURE_TOLERANCE,
    MODE_ABOVE,
    MODE_BELOW,
    MODE_LINEAR,
    NUMERIC_SENSOR_DOMAINS,
    PROF_AIRFLOW_MAP,
    PROF_CONTROL_TYPE,
    PROF_DAY_MAX,
    PROF_MAX_AIRFLOW,
    PROF_NIGHT_MAX,
    PROF_READ_ONLY,
    PROF_RULES,
    ROLE_EXHAUST,
    ROLE_INTAKE,
    RULE_DELAY,
    RULE_ENTITY,
    RULE_HYSTERESIS,
    RULE_MODE,
    RULE_MODES,
    RULE_ON_UNAVAILABLE,
    RULE_OUTPUT,
    RULE_OUTPUT_LOW,
    RULE_THRESHOLD,
    RULE_THRESHOLD_ENTITY,
    RULE_THRESHOLD_HIGH,
    RULE_THRESHOLD_HIGH_ENTITY,
    SENSOR_DOMAINS,
    UNAVAILABLE_INACTIVE,
    UNAVAILABLE_POLICIES,
)
from .models import FanModel, parse_airflow_map

_LOGGER = logging.getLogger(__name__)

# Keys that make up the full, flat config/options dict (same shape as
# entry.data / entry.options), used by the export/import steps.
_EXPORT_KEYS: tuple[str, ...] = (
    CONF_INTAKE_FANS, CONF_EXHAUST_FANS, CONF_INTAKE_PROFILES, CONF_EXHAUST_PROFILES,
    CONF_PRESENCE, CONF_MIN_HOME_STEP, CONF_MIN_AWAY_STEP, CONF_MIN_HOME_PCT,
    CONF_MIN_AWAY_PCT, CONF_NIGHT_START, CONF_NIGHT_END, CONF_INTERVAL,
    CONF_PRESSURE_TOLERANCE, CONF_MAX_CORRECTION_SECONDS,
)


def _validate_full_config(data: Any) -> list[str]:
    """Validate an imported config dict; return a list of problems (empty = valid).

    Reuses FanModel.from_profile for every fan profile so the exact same
    rules (airflow maps, rule thresholds, control types...) apply as during
    the normal wizard. Does not check that referenced entities actually
    exist in this Home Assistant instance -- same as the regular wizard,
    that is left to run time.
    """
    problems: list[str] = []
    if not isinstance(data, dict):
        return ["the imported configuration must be a JSON object"]

    intake = data.get(CONF_INTAKE_FANS)
    exhaust = data.get(CONF_EXHAUST_FANS)
    if not isinstance(intake, list) or not isinstance(exhaust, list):
        problems.append("intake_fans/exhaust_fans must be lists of entity ids")
        intake, exhaust = intake or [], exhaust or []
    elif not intake and not exhaust:
        problems.append("at least one intake or exhaust fan is required")
    elif set(intake) & set(exhaust):
        problems.append("a fan cannot be both intake and exhaust")

    intake_profiles = data.get(CONF_INTAKE_PROFILES, {})
    exhaust_profiles = data.get(CONF_EXHAUST_PROFILES, {})
    if not isinstance(intake_profiles, dict) or not isinstance(exhaust_profiles, dict):
        problems.append("intake_profiles/exhaust_profiles must be objects")
    else:
        for role, fans, profiles in (
            (ROLE_INTAKE, intake, intake_profiles),
            (ROLE_EXHAUST, exhaust, exhaust_profiles),
        ):
            for entity_id in fans:
                profile = profiles.get(entity_id)
                if not profile:
                    problems.append(f"missing profile for {entity_id}")
                    continue
                try:
                    FanModel.from_profile(entity_id, role, profile)
                except (KeyError, TypeError, ValueError) as err:
                    problems.append(f"{entity_id}: {err}")

    def _num(key: str, lo: float, hi: float) -> None:
        value = data.get(key)
        if value is None:
            problems.append(f"{key} is required")
        elif not isinstance(value, (int, float)) or isinstance(value, bool) or not lo <= value <= hi:
            problems.append(f"{key} must be a number between {lo} and {hi}")

    _num(CONF_MIN_HOME_STEP, 0, 100)
    _num(CONF_MIN_AWAY_STEP, 0, 100)
    _num(CONF_MIN_HOME_PCT, 0, 100)
    _num(CONF_MIN_AWAY_PCT, 0, 100)
    _num(CONF_INTERVAL, MIN_INTERVAL, MAX_INTERVAL)
    _num(CONF_PRESSURE_TOLERANCE, MIN_PRESSURE_TOLERANCE, MAX_PRESSURE_TOLERANCE)
    _num(CONF_MAX_CORRECTION_SECONDS, MIN_CORRECTION_SECONDS, MAX_CORRECTION_SECONDS_LIMIT)

    for key in (CONF_PRESENCE, CONF_NIGHT_START, CONF_NIGHT_END):
        value = data.get(key)
        if value is not None and not isinstance(value, str):
            problems.append(f"{key} must be a string entity id")

    if bool(data.get(CONF_NIGHT_START)) != bool(data.get(CONF_NIGHT_END)):
        problems.append("night_start and night_end must both be set or both left empty")

    return problems


def _optional(key: str, value: Any = None) -> vol.Optional:
    """Optional field whose stored value is pre-filled but can be cleared."""
    if value is None:
        return vol.Optional(key)
    return vol.Optional(key, description={"suggested_value": value})


def _number(
    minimum: float | None = None,
    maximum: float | None = None,
    step: float | str = 1,
    unit: str | None = None,
) -> NumberSelector:
    config: dict[str, Any] = {"step": step, "mode": NumberSelectorMode.BOX}
    if minimum is not None:
        config["min"] = minimum
    if maximum is not None:
        config["max"] = maximum
    if unit is not None:
        config["unit_of_measurement"] = unit
    # mypy cannot verify a dynamically-built dict against the TypedDict's
    # keys; the keys above are exactly NumberSelectorConfig's own fields.
    return NumberSelector(NumberSelectorConfig(**config))  # type: ignore[typeddict-item]


def _select(options: list[str], translation_key: str) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(
            options=options,
            mode=SelectSelectorMode.DROPDOWN,
            translation_key=translation_key,
        )
    )


class VAVFlowMixin:
    """Wizard logic shared by ConfigFlow and OptionsFlow.

    Declares the attributes it relies on from whichever FlowHandler
    subclass (ConfigFlow / OptionsFlow) it actually gets mixed into, purely
    so mypy can type-check this class in isolation; nothing here assigns
    them.
    """

    hass: HomeAssistant
    _data: dict[str, Any]
    _queue: list[tuple[str, str]]
    _current: tuple[str, str]
    _draft: dict[str, Any]
    _rules_first_visit: bool

    # ------------------------------------------------------------------
    def _init_state(self, base: dict[str, Any]) -> None:
        self._data = deepcopy(base)
        self._queue = []
        self._current = (ROLE_INTAKE, "")
        self._draft = {}
        self._rules_first_visit = True

    def _profiles(self, role: str) -> dict[str, Any]:
        key = CONF_INTAKE_PROFILES if role == ROLE_INTAKE else CONF_EXHAUST_PROFILES
        return self._data.setdefault(key, {})

    async def _finish(self) -> ConfigFlowResult:  # pragma: no cover - overridden
        raise NotImplementedError

    # ------------------------------------------------------------------
    # STEP 1: discovery
    # ------------------------------------------------------------------
    async def _discovery(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            intake = list(user_input.get(CONF_INTAKE_FANS, []))
            exhaust = list(user_input.get(CONF_EXHAUST_FANS, []))
            if not intake and not exhaust:
                errors["base"] = "no_fans"
            elif set(intake) & set(exhaust):
                errors["base"] = "fan_in_both_roles"
            else:
                _LOGGER.debug(
                    "Config flow: discovery intake=%s exhaust=%s", intake, exhaust
                )
                # quality scale: test-before-configure. There is no
                # "connection" to test for pure entity references, and the
                # whole integration is deliberately tolerant of entities
                # that are only temporarily unavailable -- but an entity
                # that does not exist in this Home Assistant instance at
                # all (never seen, e.g. a typo or a stale import) is worth
                # a heads-up without blocking the wizard, since it may
                # simply not have loaded yet.
                missing = [
                    e for e in (*intake, *exhaust) if self.hass.states.get(e) is None
                ]
                if missing:
                    _LOGGER.warning(
                        "Config flow: selected entities not currently found in "
                        "this Home Assistant instance: %s", missing,
                    )
                self._data[CONF_INTAKE_FANS] = intake
                self._data[CONF_EXHAUST_FANS] = exhaust
                # Drop profiles of fans that are no longer selected.
                for key, fans in (
                    (CONF_INTAKE_PROFILES, intake),
                    (CONF_EXHAUST_PROFILES, exhaust),
                ):
                    old = self._data.get(key, {})
                    self._data[key] = {k: v for k, v in old.items() if k in fans}
                self._queue = [(ROLE_INTAKE, e) for e in intake] + [
                    ("exhaust", e) for e in exhaust
                ]
                return await self._next_fan()

        defaults = {**self._data, **(user_input or {})}
        selector = EntitySelector(EntitySelectorConfig(domain="fan", multiple=True))
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_INTAKE_FANS, default=defaults.get(CONF_INTAKE_FANS, [])
                ): selector,
                vol.Optional(
                    CONF_EXHAUST_FANS, default=defaults.get(CONF_EXHAUST_FANS, [])
                ): selector,
            }
        )
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id=step_id, data_schema=schema, errors=errors
        )

    # ------------------------------------------------------------------
    # STEP 2..M: per-fan loop
    # ------------------------------------------------------------------
    async def _next_fan(self) -> ConfigFlowResult:
        if not self._queue:
            return await self.async_step_global()
        self._current = self._queue.pop(0)
        role, entity_id = self._current
        existing = self._profiles(role).get(entity_id)
        self._draft = deepcopy(existing) if existing else {PROF_RULES: []}
        self._draft.setdefault(PROF_RULES, [])
        self._rules_first_visit = True
        _LOGGER.debug("Config flow: profiling %s fan %s", role, entity_id)
        return await self.async_step_fan_type()

    def _placeholders(self, **extra: Any) -> dict[str, str]:
        role, entity_id = self._current
        base = {"fan": entity_id, "role": role, "remaining": str(len(self._queue))}
        base.update({k: str(v) for k, v in extra.items()})
        return base

    async def async_step_fan_type(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Control type: discrete steps or smooth percentage; read-only flag."""
        if user_input is not None:
            new_type = user_input[PROF_CONTROL_TYPE]
            if self._draft.get(PROF_CONTROL_TYPE) not in (None, new_type):
                # Old numbers/rules are meaningless for another control type.
                for key in (PROF_AIRFLOW_MAP, PROF_MAX_AIRFLOW, PROF_NIGHT_MAX):
                    self._draft.pop(key, None)
                self._draft[PROF_RULES] = []
            self._draft[PROF_CONTROL_TYPE] = new_type
            self._draft[PROF_READ_ONLY] = bool(user_input.get(PROF_READ_ONLY, False))
            return await self.async_step_fan_performance()

        schema = vol.Schema(
            {
                vol.Required(
                    PROF_CONTROL_TYPE,
                    default=self._draft.get(PROF_CONTROL_TYPE, CONTROL_STEPS),
                ): _select([CONTROL_STEPS, CONTROL_PERCENTAGE], "control_type"),
                vol.Optional(
                    PROF_READ_ONLY, default=self._draft.get(PROF_READ_ONLY, False)
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="fan_type", data_schema=schema,
            description_placeholders=self._placeholders(),
        )

    async def async_step_fan_performance(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Airflow map (steps) or max airflow (percentage) + night/day ceilings."""
        errors: dict[str, str] = {}
        is_steps = self._draft[PROF_CONTROL_TYPE] == CONTROL_STEPS
        read_only = self._draft.get(PROF_READ_ONLY, False)

        if user_input is not None:
            night = None if read_only else user_input.get(PROF_NIGHT_MAX)
            day = None if read_only else user_input.get(PROF_DAY_MAX)
            try:
                if is_steps:
                    amap = parse_airflow_map(user_input[PROF_AIRFLOW_MAP])
                    top = len(amap) - 1
                    if night is not None and not 0 <= float(night) <= top:
                        errors[PROF_NIGHT_MAX] = "night_out_of_range"
                    if day is not None and not 0 <= float(day) <= top:
                        errors[PROF_DAY_MAX] = "day_out_of_range"
                else:
                    max_air = float(user_input[PROF_MAX_AIRFLOW])
                    if max_air <= 0:
                        raise ValueError("max airflow must be positive")
            except (KeyError, TypeError, ValueError) as err:
                _LOGGER.debug("Config flow: performance validation failed: %s", err)
                errors[PROF_AIRFLOW_MAP if is_steps else PROF_MAX_AIRFLOW] = (
                    "invalid_airflow_map" if is_steps else "invalid_max_airflow"
                )

            if not errors:
                if is_steps:
                    self._draft[PROF_AIRFLOW_MAP] = amap
                    self._draft.pop(PROF_MAX_AIRFLOW, None)
                else:
                    self._draft[PROF_MAX_AIRFLOW] = max_air
                    self._draft.pop(PROF_AIRFLOW_MAP, None)
                if night is None:
                    self._draft.pop(PROF_NIGHT_MAX, None)
                else:
                    self._draft[PROF_NIGHT_MAX] = int(night) if is_steps else float(night)
                if day is None:
                    self._draft.pop(PROF_DAY_MAX, None)
                else:
                    self._draft[PROF_DAY_MAX] = int(day) if is_steps else float(day)
                _LOGGER.debug("Config flow: performance stored: %s", self._draft)
                if read_only:
                    # A read-only fan is never commanded, so rules (which
                    # only ever produce a demanded *command*) are pointless.
                    self._draft[PROF_RULES] = []
                    return await self._finish_fan()
                return await self.async_step_fan_rules()

        night_default = (user_input or {}).get(PROF_NIGHT_MAX, self._draft.get(PROF_NIGHT_MAX))
        day_default = (user_input or {}).get(PROF_DAY_MAX, self._draft.get(PROF_DAY_MAX))
        if is_steps:
            existing = self._draft.get(PROF_AIRFLOW_MAP) or [0, 30, 60, 90]
            default_map = (user_input or {}).get(
                PROF_AIRFLOW_MAP, ",".join(f"{v:g}" for v in existing)
            )
            fields: dict[Any, Any] = {
                vol.Required(PROF_AIRFLOW_MAP, default=default_map): TextSelector(),
            }
            if not read_only:
                fields[_optional(PROF_DAY_MAX, day_default)] = _number(0, 100, 1)
                fields[_optional(PROF_NIGHT_MAX, night_default)] = _number(0, 100, 1)
        else:
            fields = {
                vol.Required(
                    PROF_MAX_AIRFLOW,
                    default=(user_input or {}).get(
                        PROF_MAX_AIRFLOW, self._draft.get(PROF_MAX_AIRFLOW, 100)
                    ),
                ): _number(1, 100000, "any", "m³/h"),
            }
            if not read_only:
                fields[_optional(PROF_DAY_MAX, day_default)] = _number(0, 100, 1, "%")
                fields[_optional(PROF_NIGHT_MAX, night_default)] = _number(0, 100, 1, "%")
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="fan_performance", data_schema=vol.Schema(fields), errors=errors,
            description_placeholders=self._placeholders(),
        )

    def _max_level(self) -> int:
        if self._draft[PROF_CONTROL_TYPE] == CONTROL_STEPS:
            return len(self._draft[PROF_AIRFLOW_MAP]) - 1
        return 100

    def _build_rule(
        self, user_input: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, dict[str, str]]:
        """Validate one rule form; return (rule, errors)."""
        errors: dict[str, str] = {}
        is_steps = self._draft[PROF_CONTROL_TYPE] == CONTROL_STEPS
        max_level = self._max_level()
        mode = user_input.get(RULE_MODE, MODE_ABOVE)
        threshold = user_input.get(RULE_THRESHOLD)
        threshold_high = user_input.get(RULE_THRESHOLD_HIGH)
        threshold_entity = user_input.get(RULE_THRESHOLD_ENTITY)
        threshold_high_entity = user_input.get(RULE_THRESHOLD_HIGH_ENTITY)
        output = user_input.get(RULE_OUTPUT)
        output_low = user_input.get(RULE_OUTPUT_LOW)

        needs_threshold = mode in (MODE_ABOVE, MODE_BELOW, MODE_LINEAR)
        if needs_threshold and threshold is None and not threshold_entity:
            errors[RULE_THRESHOLD] = "threshold_required"
        if mode == MODE_LINEAR and not errors:
            if threshold_high is None and not threshold_high_entity:
                errors[RULE_THRESHOLD_HIGH] = "threshold_high_required"
            elif (
                threshold is not None and threshold_high is not None
                and not threshold_entity and not threshold_high_entity
                and float(threshold_high) <= float(threshold)
            ):
                errors[RULE_THRESHOLD_HIGH] = "threshold_high_invalid"
        if output is None or not 0 <= float(output) <= max_level:
            errors[RULE_OUTPUT] = "output_out_of_range"
        if output_low is not None and not 0 <= float(output_low) <= max_level:
            errors[RULE_OUTPUT_LOW] = "output_out_of_range"
        if errors:
            return None, errors
        assert output is not None  # the output_out_of_range check above covers this

        def _lvl(value: float) -> float:
            return int(round(float(value))) if is_steps else float(value)

        rule: dict[str, Any] = {
            RULE_ENTITY: user_input[RULE_ENTITY],
            RULE_MODE: mode,
            RULE_OUTPUT: _lvl(output),
            RULE_ON_UNAVAILABLE: user_input.get(RULE_ON_UNAVAILABLE, UNAVAILABLE_INACTIVE),
        }
        delay = user_input.get(RULE_DELAY)
        if delay:
            rule[RULE_DELAY] = min(float(delay), MAX_RULE_DELAY)
        hysteresis = user_input.get(RULE_HYSTERESIS)
        if hysteresis and mode in (MODE_ABOVE, MODE_BELOW, MODE_LINEAR):
            rule[RULE_HYSTERESIS] = min(float(hysteresis), MAX_RULE_HYSTERESIS)
        # A threshold entity always wins over a fixed number for that bound.
        if threshold_entity:
            rule[RULE_THRESHOLD_ENTITY] = threshold_entity
        elif threshold is not None:
            rule[RULE_THRESHOLD] = float(threshold)
        if mode == MODE_LINEAR:
            if threshold_high_entity:
                rule[RULE_THRESHOLD_HIGH_ENTITY] = threshold_high_entity
            elif threshold_high is not None:
                rule[RULE_THRESHOLD_HIGH] = float(threshold_high)
            if output_low is not None:
                rule[RULE_OUTPUT_LOW] = _lvl(output_low)
        return rule, {}

    async def async_step_fan_rules(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add sensor rules (repeatable). Empty sensor field = no more rules."""
        errors: dict[str, str] = {}
        existing_rules: list[dict[str, Any]] = self._draft.get(PROF_RULES, [])
        show_reset = self._rules_first_visit and bool(existing_rules)

        if user_input is not None:
            rules = list(existing_rules)
            if show_reset and user_input.get(FORM_RESET_RULES):
                rules = []
            if user_input.get(RULE_ENTITY):
                rule, errors = self._build_rule(user_input)
                if rule is not None:
                    rules.append(rule)
                    _LOGGER.debug("Config flow: rule added: %s", rule)
            if not errors:
                self._draft[PROF_RULES] = rules
                self._rules_first_visit = False
                if not user_input.get(FORM_ADD_ANOTHER):
                    return await self._finish_fan()
                # add another -> fall through and show an empty form again
                user_input = None
                existing_rules = rules
                show_reset = False

        fields: dict[Any, Any] = {}
        if show_reset:
            fields[vol.Optional(FORM_RESET_RULES, default=False)] = BooleanSelector()
        fields[_optional(RULE_ENTITY)] = EntitySelector(
            EntitySelectorConfig(domain=SENSOR_DOMAINS)
        )
        fields[vol.Optional(RULE_MODE, default=MODE_ABOVE)] = _select(RULE_MODES, "rule_mode")
        fields[_optional(RULE_THRESHOLD)] = _number(step="any")
        fields[_optional(RULE_THRESHOLD_ENTITY)] = EntitySelector(
            EntitySelectorConfig(domain=NUMERIC_SENSOR_DOMAINS)
        )
        fields[_optional(RULE_THRESHOLD_HIGH)] = _number(step="any")
        fields[_optional(RULE_THRESHOLD_HIGH_ENTITY)] = EntitySelector(
            EntitySelectorConfig(domain=NUMERIC_SENSOR_DOMAINS)
        )
        fields[_optional(RULE_HYSTERESIS, (user_input or {}).get(RULE_HYSTERESIS))] = _number(
            0, MAX_RULE_HYSTERESIS, "any"
        )
        fields[_optional(RULE_OUTPUT)] = _number(0, self._max_level(), 1)
        fields[_optional(RULE_OUTPUT_LOW)] = _number(0, self._max_level(), 1)
        fields[vol.Optional(RULE_ON_UNAVAILABLE, default=UNAVAILABLE_INACTIVE)] = _select(
            UNAVAILABLE_POLICIES, "unavailable_policy"
        )
        fields[_optional(RULE_DELAY, (user_input or {}).get(RULE_DELAY))] = _number(
            0, MAX_RULE_DELAY, 1, "s"
        )
        fields[vol.Optional(FORM_ADD_ANOTHER, default=False)] = BooleanSelector()

        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="fan_rules", data_schema=vol.Schema(fields), errors=errors,
            description_placeholders=self._placeholders(
                rules=len(existing_rules), max_level=self._max_level()
            ),
        )

    async def _finish_fan(self) -> ConfigFlowResult:
        role, entity_id = self._current
        self._profiles(role)[entity_id] = self._draft
        _LOGGER.info(
            "Config flow: stored profile of %s fan %s (%d rule(s))",
            role, entity_id, len(self._draft.get(PROF_RULES, [])),
        )
        return await self._next_fan()

    # ------------------------------------------------------------------
    # FINAL STEP: global variables
    # ------------------------------------------------------------------
    async def async_step_global(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            start = user_input.get(CONF_NIGHT_START)
            end = user_input.get(CONF_NIGHT_END)
            if bool(start) != bool(end):
                errors["base"] = "night_pair_required"
            else:
                for key in (CONF_PRESENCE, CONF_NIGHT_START, CONF_NIGHT_END):
                    if user_input.get(key):
                        self._data[key] = user_input[key]
                    else:
                        self._data.pop(key, None)
                self._data[CONF_MIN_HOME_STEP] = int(user_input[CONF_MIN_HOME_STEP])
                self._data[CONF_MIN_AWAY_STEP] = int(user_input[CONF_MIN_AWAY_STEP])
                self._data[CONF_MIN_HOME_PCT] = float(user_input[CONF_MIN_HOME_PCT])
                self._data[CONF_MIN_AWAY_PCT] = float(user_input[CONF_MIN_AWAY_PCT])
                self._data[CONF_INTERVAL] = int(user_input[CONF_INTERVAL])
                self._data[CONF_PRESSURE_TOLERANCE] = float(
                    user_input[CONF_PRESSURE_TOLERANCE]
                )
                self._data[CONF_MAX_CORRECTION_SECONDS] = int(
                    user_input[CONF_MAX_CORRECTION_SECONDS]
                )
                _LOGGER.info("Config flow: global variables stored, finishing wizard")
                return await self._finish()

        d = {**self._data, **(user_input or {})}
        schema = vol.Schema(
            {
                _optional(CONF_PRESENCE, d.get(CONF_PRESENCE)): EntitySelector(
                    EntitySelectorConfig(domain="binary_sensor")
                ),
                vol.Required(
                    CONF_MIN_HOME_STEP, default=d.get(CONF_MIN_HOME_STEP, DEFAULT_MIN_HOME_STEP)
                ): _number(0, 100, 1),
                vol.Required(
                    CONF_MIN_AWAY_STEP, default=d.get(CONF_MIN_AWAY_STEP, DEFAULT_MIN_AWAY_STEP)
                ): _number(0, 100, 1),
                vol.Required(
                    CONF_MIN_HOME_PCT, default=d.get(CONF_MIN_HOME_PCT, DEFAULT_MIN_HOME_PCT)
                ): _number(0, 100, 1, "%"),
                vol.Required(
                    CONF_MIN_AWAY_PCT, default=d.get(CONF_MIN_AWAY_PCT, DEFAULT_MIN_AWAY_PCT)
                ): _number(0, 100, 1, "%"),
                _optional(CONF_NIGHT_START, d.get(CONF_NIGHT_START)): EntitySelector(
                    EntitySelectorConfig(domain="input_datetime")
                ),
                _optional(CONF_NIGHT_END, d.get(CONF_NIGHT_END)): EntitySelector(
                    EntitySelectorConfig(domain="input_datetime")
                ),
                vol.Required(
                    CONF_INTERVAL, default=d.get(CONF_INTERVAL, DEFAULT_INTERVAL)
                ): _number(MIN_INTERVAL, MAX_INTERVAL, 1, "s"),
                vol.Required(
                    CONF_PRESSURE_TOLERANCE,
                    default=d.get(CONF_PRESSURE_TOLERANCE, DEFAULT_PRESSURE_TOLERANCE),
                ): _number(MIN_PRESSURE_TOLERANCE, MAX_PRESSURE_TOLERANCE, "any", "m³/h"),
                vol.Required(
                    CONF_MAX_CORRECTION_SECONDS,
                    default=d.get(CONF_MAX_CORRECTION_SECONDS, DEFAULT_MAX_CORRECTION_SECONDS),
                ): _number(MIN_CORRECTION_SECONDS, MAX_CORRECTION_SECONDS_LIMIT, 1, "s"),
            }
        )
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="global", data_schema=schema, errors=errors
        )

    # ------------------------------------------------------------------
    # Shared: enter the step-by-step wizard / import an exported config
    # ------------------------------------------------------------------
    async def async_step_wizard(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Entry point for the normal multi-step wizard (menu option)."""
        return await self._discovery("wizard", user_input)

    async def async_step_import_config(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Paste a previously exported configuration and use it as-is."""
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}
        if user_input is not None:
            raw = user_input.get("config_json", "")
            try:
                parsed = json.loads(raw)
            except (json.JSONDecodeError, TypeError) as err:
                errors["config_json"] = "invalid_json"
                placeholders["detail"] = str(err)
            else:
                problems = _validate_full_config(parsed)
                if problems:
                    errors["config_json"] = "invalid_config"
                    placeholders["detail"] = "; ".join(problems)
                    _LOGGER.warning(
                        "Config flow: import validation failed: %s", problems
                    )
                else:
                    self._data = parsed
                    _LOGGER.info("Config flow: configuration imported successfully")
                    return await self._finish()

        schema = vol.Schema(
            {
                vol.Required("config_json"): TextSelector(
                    TextSelectorConfig(multiline=True)
                )
            }
        )
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="import_config", data_schema=schema, errors=errors,
            description_placeholders=placeholders,
        )


class VAVConfigFlow(VAVFlowMixin, ConfigFlow, domain=DOMAIN):
    """Initial setup wizard."""

    VERSION = 1

    def __init__(self) -> None:
        self._init_state({})

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: Any) -> "OptionsFlowHandler":
        return OptionsFlowHandler()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        return self.async_show_menu(  # type: ignore[attr-defined]
            step_id="user", menu_options=["wizard", "import_config"]
        )

    async def _finish(self) -> ConfigFlowResult:
        return self.async_create_entry(title=DEFAULT_NAME, data=self._data)


class OptionsFlowHandler(VAVFlowMixin, OptionsFlow):
    """Runtime reconfiguration: replays the wizard with stored defaults."""

    def __init__(self) -> None:
        self._init_state({})
        self._loaded = False

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if not self._loaded:
            entry = self.config_entry
            base = dict(entry.options) if entry.options else dict(entry.data)
            self._init_state(base)
            self._loaded = True
        return self.async_show_menu(  # type: ignore[attr-defined]
            step_id="init", menu_options=["wizard", "export_config", "import_config"]
        )

    async def async_step_export_config(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the current configuration as JSON for the user to copy."""
        if user_input is not None:
            # Nothing to apply -- this step only displays the current
            # configuration, it never changes it. Leave the entry as-is.
            return self.async_create_entry(title="", data=self._data)

        export_text = json.dumps(
            {k: self._data[k] for k in _EXPORT_KEYS if k in self._data},
            indent=2, ensure_ascii=False, sort_keys=True,
        )
        schema = vol.Schema(
            {
                vol.Optional(
                    "config_json", default=export_text
                ): TextSelector(TextSelectorConfig(multiline=True))
            }
        )
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="export_config", data_schema=schema
        )

    async def _finish(self) -> ConfigFlowResult:
        return self.async_create_entry(title="", data=self._data)
