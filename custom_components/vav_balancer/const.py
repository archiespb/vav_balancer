"""Constants for the VAV Ventilation Balancer integration."""
from __future__ import annotations

from typing import Final

DOMAIN: Final = "vav_balancer"
PLATFORMS: Final = ["fan", "sensor", "binary_sensor"]
DEFAULT_NAME: Final = "VAV Balancer"

# ---------------------------------------------------------------------------
# Roles and control types
# ---------------------------------------------------------------------------
ROLE_INTAKE: Final = "intake"
ROLE_EXHAUST: Final = "exhaust"

CONTROL_STEPS: Final = "steps"
CONTROL_PERCENTAGE: Final = "percentage"

# ---------------------------------------------------------------------------
# Rule modes
#   above  - active when numeric value  > threshold
#   below  - active when numeric value  < threshold
#   on     - active when binary state is "on"-like
#   off    - active when binary state is "off"-like
#   linear - active when value >= threshold; output is interpolated between
#            output_low (at threshold) and output (at threshold_high)
# ---------------------------------------------------------------------------
MODE_ABOVE: Final = "above"
MODE_BELOW: Final = "below"
MODE_ON: Final = "on"
MODE_OFF: Final = "off"
MODE_LINEAR: Final = "linear"
RULE_MODES: Final = [MODE_ABOVE, MODE_BELOW, MODE_ON, MODE_OFF, MODE_LINEAR]

# What to do when a rule sensor is unavailable/unknown
UNAVAILABLE_INACTIVE: Final = "inactive"  # neutral: the rule does not fire
UNAVAILABLE_ACTIVE: Final = "active"  # safety: the rule fires with its output
UNAVAILABLE_POLICIES: Final = [UNAVAILABLE_INACTIVE, UNAVAILABLE_ACTIVE]

# ---------------------------------------------------------------------------
# Config entry keys (top level)
# ---------------------------------------------------------------------------
CONF_INTAKE_FANS: Final = "intake_fans"
CONF_EXHAUST_FANS: Final = "exhaust_fans"
CONF_INTAKE_PROFILES: Final = "intake_profiles"
CONF_EXHAUST_PROFILES: Final = "exhaust_profiles"

# Global variables
CONF_PRESENCE: Final = "presence_entity"
CONF_MIN_HOME_STEP: Final = "min_home_step"
CONF_MIN_AWAY_STEP: Final = "min_away_step"
CONF_MIN_HOME_PCT: Final = "min_home_pct"
CONF_MIN_AWAY_PCT: Final = "min_away_pct"
CONF_NIGHT_START: Final = "night_start"
CONF_NIGHT_END: Final = "night_end"
CONF_INTERVAL: Final = "interval"
CONF_PRESSURE_TOLERANCE: Final = "pressure_tolerance"
CONF_MAX_CORRECTION_SECONDS: Final = "max_correction_seconds"

# ---------------------------------------------------------------------------
# Fan profile keys
# ---------------------------------------------------------------------------
PROF_CONTROL_TYPE: Final = "control_type"
PROF_AIRFLOW_MAP: Final = "airflow_map"  # list[float], index == step
PROF_MAX_AIRFLOW: Final = "max_airflow"  # float, m3/h at 100 %
PROF_NIGHT_MAX: Final = "night_max"  # step index or percent (optional)
PROF_DAY_MAX: Final = "day_max"  # step index or percent (optional), applies outside the night window
PROF_READ_ONLY: Final = "read_only"  # bool: never commanded, only measured
PROF_RULES: Final = "rules"  # list[dict]

# ---------------------------------------------------------------------------
# Rule keys
# ---------------------------------------------------------------------------
RULE_ENTITY: Final = "entity_id"
RULE_MODE: Final = "mode"
RULE_THRESHOLD: Final = "threshold"
RULE_THRESHOLD_HIGH: Final = "threshold_high"
RULE_THRESHOLD_ENTITY: Final = "threshold_entity"
RULE_THRESHOLD_HIGH_ENTITY: Final = "threshold_high_entity"
RULE_OUTPUT: Final = "output"
RULE_OUTPUT_LOW: Final = "output_low"
RULE_ON_UNAVAILABLE: Final = "on_unavailable"
RULE_DELAY: Final = "delay_seconds"  # seconds the active/inactive state must
# hold steady before the rule's output is allowed to take effect (debounce)
RULE_HYSTERESIS: Final = "hysteresis"  # value-gap: once active, a rule stays
# active until the sensor moves this far back past the threshold (prevents
# chattering when the raw value hovers right at the threshold)

# Form-only helper fields (never persisted)
FORM_ADD_ANOTHER: Final = "add_another"
FORM_RESET_RULES: Final = "reset_rules"

# ---------------------------------------------------------------------------
# Defaults and limits
# ---------------------------------------------------------------------------
DEFAULT_MIN_HOME_STEP: Final = 1
DEFAULT_MIN_AWAY_STEP: Final = 0
DEFAULT_MIN_HOME_PCT: Final = 20.0
DEFAULT_MIN_AWAY_PCT: Final = 0.0
DEFAULT_INTERVAL: Final = 30
MIN_INTERVAL: Final = 5
MAX_INTERVAL: Final = 3600

# Pressure-safety override: when intake has no more headroom (its targets
# are already pinned at their ceiling) and actual exhaust still outruns
# actual intake by more than this, exhaust fans jump straight to their
# (already correctly capped) target instead of the normal gradual slew.
DEFAULT_PRESSURE_TOLERANCE: Final = 15.0  # m3/h
MIN_PRESSURE_TOLERANCE: Final = 0.0
MAX_PRESSURE_TOLERANCE: Final = 500.0

# Stuck-fan watchdog: if a fan's *actual* level has not moved at all for
# this long while it still deviates from its target, force it straight to
# the target on the next tick, bypassing the normal slew limit.
DEFAULT_MAX_CORRECTION_SECONDS: Final = 180
MIN_CORRECTION_SECONDS: Final = 30
MAX_CORRECTION_SECONDS_LIMIT: Final = 3600

# Per-rule activation delay (debounce) limits.
DEFAULT_RULE_DELAY: Final = 0
MAX_RULE_DELAY: Final = 7200

# Per-rule hysteresis (value gap between activation and deactivation) limits.
DEFAULT_RULE_HYSTERESIS: Final = 0.0
MAX_RULE_HYSTERESIS: Final = 1000.0

SENSOR_DOMAINS: Final = [
    "sensor",
    "binary_sensor",
    "input_boolean",
    "input_number",
    "number",
]
# Domains allowed as a dynamic threshold source (numeric only).
NUMERIC_SENSOR_DOMAINS: Final = ["sensor", "input_number", "number"]

# Slew-rate limits per execution tick
STEP_SLEW: Final = 1
PERCENT_SLEW: Final = 5.0

# Balancer numerics
EPSILON: Final = 0.5  # m3/h considered "zero" for delta convergence
FIT_TOLERANCE: Final = 1e-6
MAX_ITERATIONS: Final = 500
