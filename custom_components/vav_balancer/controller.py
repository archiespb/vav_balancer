"""Unified master controller: instant calculation + throttled execution."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, time, timedelta
from math import copysign
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_OFF, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.event import (
    EventStateChangedData,
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.util import dt as dt_util

from .balancer import BalancePlan, FanInput, FanTarget, balance
from .const import (
    CONF_BOOST_MINUTES,
    CONF_EXHAUST_FANS,
    CONF_EXHAUST_PROFILES,
    CONF_INTAKE_FANS,
    CONF_INTAKE_PROFILES,
    CONF_INTERVAL,
    CONF_MAX_CORRECTION_SECONDS,
    CONF_MIN_AWAY_PCT,
    CONF_MIN_AWAY_STEP,
    CONF_MIN_HOME_PCT,
    CONF_MIN_HOME_STEP,
    CONF_NIGHT_END,
    CONF_NIGHT_START,
    CONF_PRESENCE,
    CONF_PRESSURE_TOLERANCE,
    DEFAULT_BOOST_MINUTES,
    DEFAULT_INTERVAL,
    DEFAULT_MAX_CORRECTION_SECONDS,
    DEFAULT_MIN_AWAY_PCT,
    DEFAULT_MIN_AWAY_STEP,
    DEFAULT_MIN_HOME_PCT,
    DEFAULT_MIN_HOME_STEP,
    DEFAULT_PRESSURE_TOLERANCE,
    PERCENT_SLEW,
    ROLE_EXHAUST,
    ROLE_INTAKE,
    STEP_SLEW,
)
from .models import FanModel, Rule

_LOGGER = logging.getLogger(__name__)

FAN_DOMAIN = "fan"


class VAVController:
    """Owns configuration, the calculated plan and the hardware tick."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.enabled = True

        self.plan: BalancePlan | None = None
        self.night = False
        self.home = True
        self.actual_intake = 0.0
        self.actual_exhaust = 0.0
        self.last_reason = "not calculated yet"
        self.last_calculation: datetime | None = None
        self.last_execution: datetime | None = None
        self.last_commands: list[dict[str, Any]] = []

        self._listeners: set[Callable[[], None]] = set()
        self._unsubs: list[Callable[[], None]] = []
        self._exec_lock = asyncio.Lock()
        self._warned: set[str] = set()
        self._rule_debug: dict[str, list[dict[str, Any]]] = {}
        # Per-rule activation delay (debounce) bookkeeping, keyed by
        # "<fan entity_id>:<rule index>": {"pending": bool, "since": dt,
        # "effective": bool}. "pending" is the raw (undelayed) active/
        # inactive state; "effective" is what actually gets used once it
        # has held steady for the rule's configured delay_seconds.
        self._rule_delay_state: dict[str, dict[str, Any]] = {}
        # Raw (pre-debounce) active/inactive state from the previous
        # evaluation of each rule, used to pick the hysteresis edge.
        self._rule_raw_active: dict[str, bool] = {}
        # Watchdog: last observed actual level per fan, and since-when that
        # fan has been both deviated from its target AND not moving at all.
        self._last_level: dict[str, float] = {}
        self._deviation_since: dict[str, datetime] = {}
        # Boost: force every controllable fan to its ceiling until this
        # point in time; None when no boost is running.
        self._boost_until: datetime | None = None
        self._boost_unsub: Callable[[], None] | None = None
        # Fans temporarily excluded from balancing at runtime (via the
        # per-fan pause switch), distinct from the config-time read_only
        # flag: reversible without reconfiguring the integration.
        self._paused_fans: set[str] = set()

        # Options take precedence over the initial data when present.
        cfg: dict[str, Any] = dict(entry.options) if entry.options else dict(entry.data)
        self._cfg = cfg
        self.interval = int(cfg.get(CONF_INTERVAL, DEFAULT_INTERVAL))
        self._presence: str | None = cfg.get(CONF_PRESENCE)
        self._night_start: str | None = cfg.get(CONF_NIGHT_START)
        self._night_end: str | None = cfg.get(CONF_NIGHT_END)
        self._min_home_step = float(cfg.get(CONF_MIN_HOME_STEP, DEFAULT_MIN_HOME_STEP))
        self._min_away_step = float(cfg.get(CONF_MIN_AWAY_STEP, DEFAULT_MIN_AWAY_STEP))
        self._min_home_pct = float(cfg.get(CONF_MIN_HOME_PCT, DEFAULT_MIN_HOME_PCT))
        self._min_away_pct = float(cfg.get(CONF_MIN_AWAY_PCT, DEFAULT_MIN_AWAY_PCT))
        self._pressure_tolerance = float(
            cfg.get(CONF_PRESSURE_TOLERANCE, DEFAULT_PRESSURE_TOLERANCE)
        )
        self._max_correction = int(
            cfg.get(CONF_MAX_CORRECTION_SECONDS, DEFAULT_MAX_CORRECTION_SECONDS)
        )
        self._boost_minutes = float(cfg.get(CONF_BOOST_MINUTES, DEFAULT_BOOST_MINUTES))

        self.intake_models = self._load_models(
            cfg.get(CONF_INTAKE_FANS, []), cfg.get(CONF_INTAKE_PROFILES, {}), ROLE_INTAKE
        )
        self.exhaust_models = self._load_models(
            cfg.get(CONF_EXHAUST_FANS, []), cfg.get(CONF_EXHAUST_PROFILES, {}), ROLE_EXHAUST
        )
        self._models = {m.entity_id: m for m in [*self.intake_models, *self.exhaust_models]}

        _LOGGER.info(
            "Config validation: %d intake fan(s), %d exhaust fan(s), interval=%ss, "
            "presence=%s, night window=%s..%s, pressure_tolerance=%.1f m3/h, "
            "max_correction=%ss",
            len(self.intake_models), len(self.exhaust_models), self.interval,
            self._presence, self._night_start, self._night_end,
            self._pressure_tolerance, self._max_correction,
        )

    # ------------------------------------------------------------------
    # Setup / teardown
    # ------------------------------------------------------------------
    def _load_models(
        self, fans: list[str], profiles: dict[str, Any], role: str
    ) -> list[FanModel]:
        models: list[FanModel] = []
        for entity_id in fans:
            profile = profiles.get(entity_id)
            if not profile:
                _LOGGER.warning(
                    "Config validation: %s fan %s has no profile, skipping", role, entity_id
                )
                continue
            try:
                model = FanModel.from_profile(entity_id, role, profile)
            except (KeyError, TypeError, ValueError) as err:
                _LOGGER.error(
                    "Config validation: invalid profile of %s fan %s: %s",
                    role, entity_id, err,
                )
                continue
            _LOGGER.debug(
                "Config validation: %s fan %s -> control=%s levels=0..%s rules=%d "
                "night_max=%s day_max=%s read_only=%s",
                role, entity_id, model.control, model.max_level, len(model.rules),
                model.night_max, model.day_max, model.read_only,
            )
            models.append(model)
        return models

    def _tracked_entities(self) -> list[str]:
        tracked: set[str] = set(self._models)
        for model in self._models.values():
            for rule in model.rules:
                tracked.add(rule.entity_id)
                if rule.threshold_entity:
                    tracked.add(rule.threshold_entity)
                if rule.threshold_high_entity:
                    tracked.add(rule.threshold_high_entity)
        for optional in (self._presence, self._night_start, self._night_end):
            if optional:
                tracked.add(optional)
        return sorted(tracked)

    async def async_start(self) -> None:
        """Register both contours and run the first calculation."""
        self._recalculate("startup")

        tracked = self._tracked_entities()
        _LOGGER.debug("Contour 1: tracking %d entities: %s", len(tracked), tracked)
        self._unsubs.append(
            async_track_state_change_event(self.hass, tracked, self._handle_state_event)
        )
        # Contour 2: hardware throttle
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._handle_tick, timedelta(seconds=self.interval)
            )
        )
        _LOGGER.info("Contour 2: hardware execution every %s s", self.interval)

    async def async_stop(self) -> None:
        """Unregister every tracker."""
        while self._unsubs:
            self._unsubs.pop()()
        if self._boost_unsub is not None:
            self._boost_unsub()
            self._boost_unsub = None
        self._listeners.clear()

    # ------------------------------------------------------------------
    # Live-tunable safety parameters (read-only here; changed by the
    # number platform through a config entry options update + reload, so
    # they persist across restarts the same way the wizard's values do)
    # ------------------------------------------------------------------
    @property
    def pressure_tolerance(self) -> float:
        return self._pressure_tolerance

    @property
    def max_correction_seconds(self) -> int:
        return self._max_correction

    # ------------------------------------------------------------------
    # Boost: force every controllable fan to its ceiling for a while
    # ------------------------------------------------------------------
    @property
    def boost_active(self) -> bool:
        return self._boost_until is not None and dt_util.utcnow() < self._boost_until

    @property
    def boost_remaining_seconds(self) -> float | None:
        if self._boost_until is None:
            return None
        remaining = (self._boost_until - dt_util.utcnow()).total_seconds()
        return max(0.0, remaining)

    @callback
    def async_start_boost(self, minutes: float | None = None) -> None:
        """Force every controllable fan to its ceiling for `minutes`."""
        duration = minutes if minutes is not None else self._boost_minutes
        if self._boost_unsub is not None:
            self._boost_unsub()
        self._boost_until = dt_util.utcnow() + timedelta(minutes=duration)
        self._boost_unsub = async_call_later(
            self.hass, timedelta(minutes=duration), self._handle_boost_end
        )
        _LOGGER.info("Boost started for %.1f minute(s)", duration)
        self._recalculate("boost started")

    @callback
    def async_cancel_boost(self) -> None:
        """End an active boost immediately, if one is running."""
        if self._boost_until is None:
            return
        if self._boost_unsub is not None:
            self._boost_unsub()
            self._boost_unsub = None
        self._boost_until = None
        _LOGGER.info("Boost cancelled")
        self._recalculate("boost cancelled")

    @callback
    def _handle_boost_end(self, now: datetime) -> None:
        self._boost_unsub = None
        self._boost_until = None
        _LOGGER.info("Boost ended")
        self._recalculate("boost ended")

    # ------------------------------------------------------------------
    # Per-fan runtime pause (reversible without reconfiguring)
    # ------------------------------------------------------------------
    def is_paused(self, entity_id: str) -> bool:
        return entity_id in self._paused_fans

    @callback
    def async_set_paused(self, entity_id: str, paused: bool) -> None:
        if paused:
            self._paused_fans.add(entity_id)
        else:
            self._paused_fans.discard(entity_id)
        _LOGGER.info("%s %s", entity_id, "paused" if paused else "resumed")
        self._recalculate("fan paused" if paused else "fan resumed")

    # ------------------------------------------------------------------
    # Entity plumbing
    # ------------------------------------------------------------------
    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.add(listener)

        @callback
        def _remove() -> None:
            self._listeners.discard(listener)

        return _remove

    @callback
    def set_enabled(self, enabled: bool) -> None:
        if self.enabled == enabled:
            return
        self.enabled = enabled
        _LOGGER.info("Hardware execution %s", "ENABLED" if enabled else "DISABLED")
        self._recalculate("enabled toggled")

    def _warn_once(self, key: str, message: str, *args: Any) -> None:
        if key in self._warned:
            return
        self._warned.add(key)
        _LOGGER.warning(message, *args)

    def _clear_warning(self, key: str) -> None:
        # quality scale: log-when-unavailable -- log once when a
        # previously-unavailable entity becomes available again, not on
        # every tick (only fires when `key` was actually warned about).
        if key in self._warned:
            self._warned.discard(key)
            _LOGGER.info("%s is available again", key)

    # ------------------------------------------------------------------
    # Contour 1: instant calculation (never touches hardware)
    # ------------------------------------------------------------------
    @callback
    def _handle_state_event(self, event: Event[EventStateChangedData]) -> None:
        entity_id = event.data.get("entity_id")
        new_state = event.data.get("new_state")
        _LOGGER.debug(
            "Contour 1: %s -> %s", entity_id, new_state.state if new_state else None
        )
        self._recalculate(f"state change: {entity_id}")

    @callback
    def _recalculate(self, reason: str) -> None:
        """Recompute targets from current states; publish; no hardware calls."""
        try:
            self.night = self._is_night()
            self.home = self._is_home()
            intakes = [self._build_input(m) for m in self.intake_models]
            exhausts = [self._build_input(m) for m in self.exhaust_models]
            self.plan = balance(intakes, exhausts)
            self.actual_intake = self._actual_flow(self.intake_models)
            self.actual_exhaust = self._actual_flow(self.exhaust_models)
            self.last_reason = reason
            self.last_calculation = dt_util.utcnow()
            _LOGGER.debug(
                "Contour 1 (%s): night=%s home=%s target in/out/delta=%.1f/%.1f/%.1f "
                "actual in/out=%.1f/%.1f",
                reason, self.night, self.home, self.plan.intake_flow,
                self.plan.exhaust_flow, self.plan.delta,
                self.actual_intake, self.actual_exhaust,
            )
        except Exception:  # noqa: BLE001 - never crash Home Assistant Core
            _LOGGER.exception("Contour 1: calculation failed, keeping previous plan")
        for listener in list(self._listeners):
            listener()

    def _resolve_bound(
        self, entity_id: str | None, static_value: float | None, warn_key: str
    ) -> tuple[float | None, bool]:
        """Resolve one threshold bound, which may come from another entity.

        Returns (value, ok). ok is False only when an entity was configured
        for this bound but its state cannot be read as a number right now;
        the fixed-number case (entity_id is None) is always ok.
        """
        if not entity_id:
            return static_value, True
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            self._warn_once(
                warn_key, "Threshold sensor %s is unavailable", entity_id
            )
            return None, False
        try:
            value = float(state.state)
        except (TypeError, ValueError):
            self._warn_once(
                warn_key, "Threshold sensor %s has a non-numeric state '%s'",
                entity_id, state.state,
            )
            return None, False
        self._clear_warning(warn_key)
        return value, True

    def _apply_delay(
        self, rule_key: str, raw_active: bool, delay_seconds: float
    ) -> bool:
        """Debounce a rule's active/inactive state.

        The moment the raw (undelayed) active/inactive state changes, a
        timer starts; if it changes back before `delay_seconds` elapses,
        the timer simply resets (classic debounce, not cumulative). Only
        once the raw state has held steady for the full delay does the
        *effective* state actually flip. delay_seconds=0 means instant,
        matching the pre-existing (undelayed) behaviour exactly.
        """
        now = dt_util.utcnow()
        state = self._rule_delay_state.get(rule_key)
        if state is None:
            state = {"pending": raw_active, "since": now, "effective": raw_active}
            self._rule_delay_state[rule_key] = state
        if raw_active != state["pending"]:
            state["pending"] = raw_active
            state["since"] = now
        if delay_seconds <= 0:
            state["effective"] = state["pending"]
        else:
            elapsed = (now - state["since"]).total_seconds()
            if elapsed >= delay_seconds:
                state["effective"] = state["pending"]
        return state["effective"]

    def _evaluate_rule(
        self, rule: Rule, rule_key: str
    ) -> tuple[float | None, dict[str, Any]]:
        """Evaluate one rule, resolving dynamic thresholds if configured."""
        rule_state = self.hass.states.get(rule.entity_id)
        skey = f"sensor:{rule.entity_id}"
        if rule_state is None or rule_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            self._warn_once(
                skey, "Sensor %s is unavailable; applying '%s' policy",
                rule.entity_id, rule.on_unavailable,
            )
        else:
            self._clear_warning(skey)

        threshold, threshold_ok = self._resolve_bound(
            rule.threshold_entity, rule.threshold,
            f"thr:{rule.entity_id}:{rule.threshold_entity}",
        )
        threshold_high, threshold_high_ok = self._resolve_bound(
            rule.threshold_high_entity, rule.threshold_high,
            f"thrh:{rule.entity_id}:{rule.threshold_high_entity}",
        )

        if not (threshold_ok and threshold_high_ok):
            level = rule.fallback()
        else:
            # Hysteresis: which edge (plain threshold, or threshold offset
            # by `rule.hysteresis`) applies depends on whether this rule
            # was already active last time -- that avoids chattering when
            # the raw value hovers right at the threshold.
            was_active = self._rule_raw_active.get(rule_key, False)
            level = rule.evaluate(
                rule_state, threshold=threshold, threshold_high=threshold_high,
                was_active=was_active,
            )

        raw_active = level is not None
        self._rule_raw_active[rule_key] = raw_active

        # Debounce: the raw active/inactive state above must hold steady
        # for `delay_seconds` before it is allowed to actually change the
        # fan's demand. Once confirmed active, the magnitude itself (for
        # linear-mode rules) still tracks the live sensor value freely —
        # only the on/off decision is delayed, not the continuous output.
        effective_active = self._apply_delay(rule_key, raw_active, rule.delay_seconds)
        effective_level = level if effective_active else None

        debug = {
            "sensor": rule.entity_id,
            "mode": rule.mode,
            "threshold": threshold,
            "threshold_source": rule.threshold_entity or "fixed",
            "threshold_high": threshold_high,
            "threshold_high_source": rule.threshold_high_entity or "fixed",
            "output": effective_level,
            "active": effective_active,
            "delay_seconds": rule.delay_seconds,
            "hysteresis": rule.hysteresis,
            "pending": raw_active != effective_active,
        }
        return effective_level, debug

    def _build_input(self, model: FanModel) -> FanInput:
        state = self.hass.states.get(model.entity_id)
        available = state is not None and state.state not in (
            STATE_UNAVAILABLE, STATE_UNKNOWN,
        )
        key = f"fan:{model.entity_id}"
        if available:
            self._clear_warning(key)
        else:
            self._warn_once(
                key, "Fan %s is unavailable; excluded from balance (safety fallback)",
                model.entity_id,
            )

        if model.read_only or model.entity_id in self._paused_fans:
            # Autonomous (config-time) or paused (runtime, reversible): not
            # commanded either way. Its *current real* level is pinned as
            # floor == ceiling == demand, so the balancer counts its actual
            # airflow towards the total but never tries to move it; the
            # other, controllable fans adjust around it.
            self._rule_debug[model.entity_id] = []
            if not available:
                return FanInput(model, False, None, 0.0, 0.0, 0.0)
            level = self._read_level(model, state)
            if level is None:
                self._warn_once(
                    f"readonly:{model.entity_id}",
                    "Read-only fan %s state is not readable; treated as 0 for balancing",
                    model.entity_id,
                )
                level = 0.0
            level = model.clamp(level)
            return FanInput(model, True, None, level, level, level)

        rule_level: float | None = None
        rule_debug: list[dict[str, Any]] = []
        for idx, rule in enumerate(model.rules):
            rule_key = f"{model.entity_id}:{idx}"
            level, debug = self._evaluate_rule(rule, rule_key)
            rule_debug.append(debug)
            if level is not None:
                rule_level = level if rule_level is None else max(rule_level, level)
        self._rule_debug[model.entity_id] = rule_debug

        ceiling = model.max_level
        if self.night and model.night_max is not None:
            ceiling = model.night_max
        elif not self.night and model.day_max is not None:
            ceiling = model.day_max
        ceiling = model.clamp(ceiling)
        floor = model.clamp(self._min_level(model), ceiling)
        if self.boost_active:
            # Force to the ceiling; the balancer still converges intake and
            # exhaust around each other normally from there.
            demand = ceiling
        else:
            demand = model.clamp(max(floor, rule_level or 0.0), ceiling)
        _LOGGER.debug(
            "Demand %s: rule=%s floor=%.1f ceiling=%.1f boost=%s -> demand=%.1f",
            model.entity_id, rule_level, floor, ceiling, self.boost_active, demand,
        )
        return FanInput(model, available, rule_level, floor, ceiling, demand)

    def _min_level(self, model: FanModel) -> float:
        if model.is_steps:
            return self._min_home_step if self.home else self._min_away_step
        return self._min_home_pct if self.home else self._min_away_pct

    def _is_home(self) -> bool:
        if not self._presence:
            return True
        state = self.hass.states.get(self._presence)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            self._warn_once(
                "presence", "Presence sensor %s unavailable; assuming HOME", self._presence
            )
            return True
        self._clear_warning("presence")
        return state.state in ("on", "home")

    def _entity_time(self, entity_id: str | None) -> time | None:
        if not entity_id:
            return None
        state = self.hass.states.get(entity_id)
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        attrs = state.attributes
        try:
            if "hour" in attrs and "minute" in attrs:
                return time(int(attrs["hour"]), int(attrs["minute"]))
            return dt_util.parse_time(str(state.state).split(" ")[-1])
        except (TypeError, ValueError):
            return None

    def _is_night(self) -> bool:
        start = self._entity_time(self._night_start)
        end = self._entity_time(self._night_end)
        if start is None or end is None or start == end:
            return False
        now = dt_util.now().time()
        if start < end:
            return start <= now < end
        return now >= start or now < end

    # ------------------------------------------------------------------
    # Actual hardware state readers
    # ------------------------------------------------------------------
    def _read_level(self, model: FanModel, state: State | None) -> float | None:
        """Current hardware level (step index / percent) or None if unknown."""
        if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        if state.state == STATE_OFF:
            return 0.0
        attrs = state.attributes
        if model.is_steps:
            steps = int(model.max_level)
            presets = attrs.get("preset_modes")
            preset = attrs.get("preset_mode")
            if presets and len(presets) == steps and preset in presets:
                return float(list(presets).index(preset) + 1)
            pct = attrs.get("percentage")
            if pct is not None:
                return float(min(steps, max(0, round(float(pct) / 100.0 * steps))))
            return None
        pct = attrs.get("percentage")
        return float(pct) if pct is not None else None

    def _actual_flow(self, models: list[FanModel]) -> float:
        total = 0.0
        for model in models:
            level = self._read_level(model, self.hass.states.get(model.entity_id))
            if level is not None:
                total += model.flow(level)
        return total

    # ------------------------------------------------------------------
    # Contour 2: throttled hardware execution
    # ------------------------------------------------------------------
    async def _handle_tick(self, now: datetime) -> None:
        # Refresh time-dependent inputs (night window) before dispatching.
        self._recalculate("execution tick")
        await self._async_execute()

    @staticmethod
    def _slew(model: FanModel, current: float, target: float) -> float:
        """Limit the change to +/-1 step or +/-5 percent per tick."""
        if model.is_steps:
            cur, tgt = int(round(current)), int(round(target))
            if tgt > cur:
                return float(cur + STEP_SLEW)
            if tgt < cur:
                return float(cur - STEP_SLEW)
            return float(cur)
        delta = target - current
        new = target if abs(delta) <= PERCENT_SLEW else current + copysign(PERCENT_SLEW, delta)
        return float(max(0, min(100, round(new))))

    async def _async_execute(self) -> None:
        if not self.enabled:
            _LOGGER.debug("Contour 2: controller disabled, skipping tick")
            return
        if self.plan is None:
            return

        now = dt_util.utcnow()
        intake_actual = self.actual_intake

        # Exhaust fans must track *actual* intake within tolerance at every
        # tick, not just react once they have already drifted too far: cap
        # their combined flow for this tick to [intake_actual - tolerance,
        # intake_actual + tolerance], then scale each exhaust fan's own
        # target proportionally. This is what keeps the two sides changing
        # in lockstep instead of each racing to its own long-run target at
        # its own native speed — a fast demand swing (e.g. a presence
        # sensor flipping on) no longer lets exhaust jump straight to a
        # target that assumes intake has *already* reached a ceiling it is
        # still several slew-limited ticks away from, in either direction.
        exhaust_targets = list(self.plan.exhaust.values())
        combined_target_flow = sum(t.flow for t in exhaust_targets)
        band_lo = intake_actual - self._pressure_tolerance
        band_hi = intake_actual + self._pressure_tolerance
        combined_goal_flow = min(max(combined_target_flow, band_lo), band_hi)
        exhaust_scale = (
            combined_goal_flow / combined_target_flow
            if combined_target_flow > 1e-9 else 1.0
        )
        if abs(exhaust_scale - 1.0) > 1e-6:
            _LOGGER.debug(
                "Contour 2: pacing exhaust to actual intake %.1f m3/h "
                "(+/-%.1f): combined target %.1f -> goal %.1f (scale %.3f)",
                intake_actual, self._pressure_tolerance,
                combined_target_flow, combined_goal_flow, exhaust_scale,
            )

        # Even with the goal itself kept in band, *reaching* it via the
        # ordinary +/-5%/+/-1-step slew can still take several minutes for a
        # large, sudden demand swing. Once the actual delta is already
        # outside tolerance, close that gap immediately rather than
        # gradually: jumping straight to `goal_level` (not the raw
        # long-run `target.level`) is always safe, because the goal is
        # already bounded to within tolerance of actual intake above.
        exhaust_urgent = (
            abs(intake_actual - self.actual_exhaust) > self._pressure_tolerance
        )
        ukey = "exhaust_urgent"
        if exhaust_urgent:
            self._warn_once(
                ukey,
                "Contour 2: actual delta is %.1f m3/h (beyond +/-%.1f "
                "tolerance); jumping exhaust fans straight to their "
                "(intake-paced) goal instead of the normal gradual ramp",
                intake_actual - self.actual_exhaust, self._pressure_tolerance,
            )
        else:
            self._clear_warning(ukey)

        async with self._exec_lock:
            operations: list[tuple[int, FanModel, State, float, float, float]] = []
            targets = [*self.plan.intake.values(), *self.plan.exhaust.values()]
            for target in targets:
                model = self._models[target.entity_id]
                if model.read_only or model.entity_id in self._paused_fans:
                    # Autonomous or paused: only ever read, never commanded.
                    continue
                state = self.hass.states.get(model.entity_id)
                current = self._read_level(model, state)
                if state is None or current is None:
                    _LOGGER.debug(
                        "Contour 2: %s state unreadable, skipping", model.entity_id
                    )
                    continue

                if model.role == ROLE_EXHAUST:
                    goal_flow = target.flow * exhaust_scale
                    if abs(goal_flow - target.flow) < 1e-6:
                        goal_level = target.level
                    else:
                        goal_level = model.level_for_flow(
                            goal_flow, round_up=(goal_flow > target.flow)
                        )
                    goal_level = model.clamp(goal_level)
                else:
                    goal_level = target.level

                # Stuck-fan watchdog: if the *actual* level has not moved at
                # all since the previous tick while still deviating from
                # its current goal, that is zero real progress — track how
                # long that has lasted. A fan that IS moving each tick
                # (ordinary slew-limited ramping) never trips this, no
                # matter how many ticks the overall ramp takes. Deviation is
                # measured against `goal_level`, NOT the raw long-run
                # `target.level`: for exhaust these two intentionally differ
                # for many ticks in a row during a paced ramp (by design,
                # see above), and comparing against the distant target would
                # make that correct, intentional pacing look like a stall
                # and force a premature jump straight to it, re-introducing
                # the exact overshoot the pacing exists to prevent.
                #
                # "Deviated" uses a wider margin than the 0.5 used below to
                # decide whether to send a command at all: many real
                # percentage fans only support a handful of discrete native
                # speeds (e.g. a report of 25.098% rather than the requested
                # 24.55%), so they can land within a percent or two of the
                # goal and then genuinely stay there forever — that is
                # convergence, not a stall, and must not accumulate stuck
                # time. One normal slew step is a reasonable "close enough"
                # band for that: it can never be mistaken for a multi-tick
                # stall, which is what this watchdog exists to catch.
                watchdog_margin = 0.5 if model.is_steps else PERCENT_SLEW
                deviated = abs(current - goal_level) >= watchdog_margin
                last_level = self._last_level.get(model.entity_id)
                self._last_level[model.entity_id] = current
                no_progress = (
                    deviated and last_level is not None
                    and abs(current - last_level) < 0.5
                )
                if not deviated or not no_progress:
                    self._deviation_since.pop(model.entity_id, None)
                    stuck_seconds = 0.0
                else:
                    since = self._deviation_since.setdefault(model.entity_id, now)
                    stuck_seconds = (now - since).total_seconds()

                force_full = deviated and stuck_seconds >= self._max_correction
                skip_slew = force_full or (
                    model.role == ROLE_EXHAUST and exhaust_urgent
                )
                if force_full:
                    _LOGGER.warning(
                        "Contour 2: %s has not moved for %.0fs while %.1f away "
                        "from its current goal (%.1f vs %.1f); forcing full "
                        "correction",
                        model.entity_id, stuck_seconds,
                        abs(current - goal_level), current, goal_level,
                    )
                if skip_slew:
                    new_level = model.clamp(goal_level)
                else:
                    new_level = self._slew(model, current, goal_level)

                if abs(new_level - current) < 0.5:
                    continue
                # Keep positive pressure while ramping: raise intake and
                # lower exhaust first, then the opposite moves. Forced /
                # urgent corrections always go first regardless of role.
                safe_first = (
                    skip_slew
                    or (model.role == ROLE_INTAKE and new_level > current)
                    or (model.role == ROLE_EXHAUST and new_level < current)
                )
                operations.append(
                    (0 if safe_first else 1, model, state, current, new_level, target.level)
                )

            operations.sort(key=lambda op: op[0])
            commands: list[dict[str, Any]] = []
            for _, model, state, current, new_level, goal in operations:
                _LOGGER.debug(
                    "Contour 2: %s %.1f -> %.1f (goal %.1f)",
                    model.entity_id, current, new_level, goal,
                )
                if await self._async_apply(model, state, new_level):
                    commands.append(
                        {"entity_id": model.entity_id, "from": current, "to": new_level,
                         "goal": goal}
                    )

            self.last_execution = dt_util.utcnow()
            self.last_commands = commands
            _LOGGER.debug("Contour 2: tick finished, %d command(s) sent", len(commands))
        for listener in list(self._listeners):
            listener()

    async def _async_apply(self, model: FanModel, state: State, level: float) -> bool:
        """Dispatch one service call; return True on success."""
        entity_id = model.entity_id
        data: dict[str, Any]
        try:
            if level <= 0:
                service, data = "turn_off", {"entity_id": entity_id}
            elif model.is_steps:
                steps = int(model.max_level)
                step = int(round(level))
                presets = state.attributes.get("preset_modes")
                if presets and len(presets) == steps:
                    service = "set_preset_mode"
                    data = {"entity_id": entity_id, "preset_mode": list(presets)[step - 1]}
                else:
                    service = "set_percentage"
                    data = {"entity_id": entity_id, "percentage": round(step * 100 / steps)}
            else:
                service = "set_percentage"
                data = {"entity_id": entity_id, "percentage": int(round(level))}

            await self.hass.services.async_call(FAN_DOMAIN, service, data, blocking=True)
            _LOGGER.debug("Contour 2: fan.%s %s", service, data)
            return True
        except Exception as err:  # noqa: BLE001 - keep the controller alive
            _LOGGER.error("Contour 2: command for %s failed: %s", entity_id, err)
            return False

    # ------------------------------------------------------------------
    # Public read access for the sensor / binary_sensor platforms
    # ------------------------------------------------------------------
    def rule_debug_for(self, entity_id: str) -> list[dict[str, Any]]:
        """Debug info of every rule of one fan, as last evaluated."""
        return self._rule_debug.get(entity_id, [])

    def target_for(self, entity_id: str) -> FanTarget | None:
        """Latest calculated target of one fan, or None before the first run."""
        if self.plan is None:
            return None
        return self.plan.intake.get(entity_id) or self.plan.exhaust.get(entity_id)

    def actual_flow_for(self, entity_id: str) -> float | None:
        """Current real-world airflow of one fan, in m3/h."""
        model = self._models.get(entity_id)
        if model is None:
            return None
        level = self._read_level(model, self.hass.states.get(entity_id))
        return None if level is None else model.flow(level)

    def stuck_seconds_for(self, entity_id: str) -> float | None:
        """How long this fan's actual level has been deviated AND not
        moving at all; None if it is currently converged or progressing."""
        since = self._deviation_since.get(entity_id)
        if since is None:
            return None
        return (dt_util.utcnow() - since).total_seconds()

    def global_metrics(self) -> dict[str, float | None]:
        """Aggregate target/actual airflow figures for dashboard sensors."""
        plan = self.plan
        return {
            "target_intake_flow": None if plan is None else plan.intake_flow,
            "target_exhaust_flow": None if plan is None else plan.exhaust_flow,
            "requested_exhaust_flow": None if plan is None else plan.exhaust_demand_flow,
            "target_delta": None if plan is None else plan.delta,
            "actual_intake_flow": self.actual_intake,
            "actual_exhaust_flow": self.actual_exhaust,
            "actual_delta": self.actual_intake - self.actual_exhaust,
        }

    # ------------------------------------------------------------------
    # Attributes for the master entity
    # ------------------------------------------------------------------
    def state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "enabled": self.enabled,
            "night_mode": self.night,
            "home_mode": self.home,
            "interval_seconds": self.interval,
            "pressure_tolerance_m3h": self._pressure_tolerance,
            "max_correction_seconds": self._max_correction,
            "boost_active": self.boost_active,
            "boost_remaining_seconds": self.boost_remaining_seconds,
            "paused_fans": sorted(self._paused_fans),
            "last_reason": self.last_reason,
            "last_calculation": self.last_calculation.isoformat()
            if self.last_calculation else None,
            "last_execution": self.last_execution.isoformat()
            if self.last_execution else None,
            "last_commands": self.last_commands[-10:],
            "actual_intake_m3h": round(self.actual_intake, 1),
            "actual_exhaust_m3h": round(self.actual_exhaust, 1),
            "actual_delta_m3h": round(self.actual_intake - self.actual_exhaust, 1),
        }
        plan = self.plan
        if plan is not None:
            def _dump(items: dict[str, Any]) -> dict[str, Any]:
                return {
                    k: {
                        "control": t.control,
                        "target_level": t.level,
                        "target_flow_m3h": round(t.flow, 1),
                        "demand_level": t.demand_level,
                        "ceiling": t.ceiling,
                        "available": t.available,
                        "read_only": self._models[k].read_only,
                        "paused": k in self._paused_fans,
                        "stuck_seconds": self.stuck_seconds_for(k),
                        "rules": self.rule_debug_for(k),
                    }
                    for k, t in items.items()
                }

            attrs.update(
                {
                    "target_intake_m3h": round(plan.intake_flow, 1),
                    "target_exhaust_m3h": round(plan.exhaust_flow, 1),
                    "requested_exhaust_m3h": round(plan.exhaust_demand_flow, 1),
                    "target_delta_m3h": round(plan.delta, 1),
                    "notes": plan.notes,
                    "intake_targets": _dump(plan.intake),
                    "exhaust_targets": _dump(plan.exhaust),
                }
            )
        return attrs
