# VAV Ventilation Balancer — full technical reference

For a quick overview, installation and first setup, see [README.md](README.md).
Русская версия: [DOCS.ru.md](DOCS.ru.md).

## Contents

- [Config wizard — full step-by-step reference](#config-wizard--full-step-by-step-reference)
- [Export and import of the configuration](#export-and-import-of-the-configuration)
- [Sensor rules](#sensor-rules)
- [Two execution contours](#two-execution-contours)
- [Read-only fans](#read-only-fans)
- [Protection against prolonged and excessive imbalance](#protection-against-prolonged-and-excessive-imbalance)
- [Balancer math](#balancer-math)
- [Fault tolerance](#fault-tolerance)
- [Master entity](#master-entity)
- [Dashboard entities](#dashboard-entities)
- [Logging and diagnostics](#logging-and-diagnostics)

## Config wizard — full step-by-step reference

Both when first adding the integration and in **Configure** on the integration's card (`OptionsFlow`), a menu is shown first:

- **Configure step by step** — the ordinary step-by-step wizard, described below;
- **Import an exported configuration** — available both on first setup and in `OptionsFlow`;
- **Export current configuration** — `OptionsFlow` only, see "Export and import of the configuration" below.

**Step 1. Fan discovery.** A multi-select of `fan` entities for intake and for exhaust.

**Steps repeated for every fan (in a loop, intake first, then exhaust):**

1. *Control type*: **discrete steps** (preset modes) or **smooth percentage** (0-100%). The same form also has a **"Read-only (autonomous fan)"** checkbox.
2. *Performance*:
   - steps: airflow in m³/h for every step, comma separated, starting at step 0 (e.g. `0,30,45,60,75,90,140`);
   - percentage: maximum airflow in m³/h at 100%.

   The same step also sets the **day ceiling** and **night ceiling** — the highest step or percentage allowed outside and during quiet hours respectively. Empty means no limit; if quiet hours aren't configured globally (see below), only the day ceiling applies.
3. *Sensor rules* (add as many as you like; leaving the sensor field empty finishes the loop). For a read-only fan, this step and both ceilings are skipped — there is nothing to follow them.

**Final step. Global variables:**

- a presence binary sensor (optional);
- "home / away" minimums, separately for step-controlled (in steps) and percentage-controlled (in %) fans;
- two `input_datetime` entities bounding the night window (set both or neither);
- hardware command interval, in seconds (5-3600);
- **pressure tolerance** (m³/h) and **max correction time** (s) — see "Protection against prolonged imbalance" below.

All of this can be changed later via **Configure** on the integration's card (`OptionsFlow`) → **Configure step by step**. The wizard replays with the stored values pre-filled, existing rules are kept (you can tick "Delete stored rules first" to clear them). The integration reloads after saving.

## Export and import of the configuration

In `OptionsFlow` (**Configure** on the integration's card), the **"Export current configuration"** menu option shows the entire current configuration (every fan, its profile, its rules, and the global settings) as a single JSON object in a text field — select and copy it. Submitting this form makes no changes to the integration; it is a view only.

The same JSON can be pasted back via **"Import an exported configuration"** — available both in the `OptionsFlow` menu (replaces the current configuration outright) and in the menu shown on first adding the integration (for example, to quickly set up another Home Assistant instance, or after a reinstall). Before being accepted, the configuration is validated with the exact same rules as the ordinary step-by-step wizard (airflow maps, rule thresholds, global parameter ranges) — on any error the form is shown again with a description of the specific problem. Entity references (fans, sensors) are not checked for existence in this Home Assistant instance on import, the same as with the ordinary wizard; this lets you, for example, prepare a configuration ahead of time.

## Sensor rules

Every rule turns a sensor's state into a **demanded level** for a fan: a step number or a percentage, depending on the control type. If several rules fire, the highest one wins.

| Mode | When it fires |
|---|---|
| `above` | number > threshold |
| `below` | number < threshold |
| `on` / `off` | binary sensor is on / off |
| `linear` | number ≥ threshold; output ramps linearly from the "level at threshold" up to the output value at the upper threshold |

For an unavailable (`unavailable` / `unknown`) sensor, each rule has its own policy: **inactive** (neutral, the rule does not fire) or **active** (safety mode, the rule fires with its configured output).

### A threshold can be another sensor's live value

The threshold (`threshold`) and the linear mode's upper threshold (`threshold_high`) don't have to be a fixed number. The rule form has two parallel fields for each bound:

- **Threshold** / **Threshold sensor** — for the lower bound;
- **Upper threshold** / **Upper threshold sensor** — for the upper bound (linear mode).

If a sensor is chosen, **it always takes priority** over the typed-in number for that same bound. A typical example is comparing not against a fixed 40% humidity, but against a virtual `sensor.average_humidity` that itself averages several rooms. A threshold sensor can be `sensor`, `input_number`, or `number` — any source of a numeric value, including template sensors and `input_number` helpers from the Home Assistant UI.

Contour 1 tracks such threshold sensors exactly like ordinary rule sensors: a change in `sensor.average_humidity`'s value triggers an immediate recalculation.

If a threshold sensor becomes `unavailable`/`unknown`, the same `on_unavailable` policy applies as for the rule's main sensor (inactive or safety output) — there is no separate policy to configure for the threshold itself.

### Activation delay (debounce)

Every rule has an optional **"Activation delay (debounce), s"** field. Until the rule's raw active/inactive state has held steady, unchanged, for at least this long, it does not affect the fan's demand — a classic debounce: any brief return to the previous state resets the timer instead of accumulating. The default is 0 (instant, the previous behaviour). Useful, for example, for a presence sensor that can flicker for a fraction of a second, or for a noisy humidity reading. Once the delay has elapsed and the rule is confirmed active, the output value itself (for linear mode) keeps freely tracking the live sensor reading — the delay only applies to the on/off decision, not to the continuous output.

### Hysteresis (activation gap) — why a fan can "hunt" at the threshold

If a sensor's raw reading (especially when the threshold itself is dynamic, e.g. computed by a separate averaging sensor across several rooms) sits near the threshold for a long time, it will almost inevitably wander around it by a fraction of a percent — from sensor noise, the fan's own airflow, convection, or the averaging sensor's own drift. Without a gap between the activation and deactivation points, the rule **flickers** active/inactive on every such micro-fluctuation: deactivation instantly drops demand to the floor, activation pulls it back up, and this shows up as a fan that keeps "speeding up then slowing down" — and not just on "its own" fan, but across the whole group, since one exhaust fan's target changing propagates to the intake fans too through the balance-maintaining mechanism (see "Balancer math" below).

The **"Hysteresis (activation gap)"** field (only for `above`/`below`/`linear` modes) fixes this: once active, a rule does not deactivate on the very first time the value recrosses the threshold — it stays active until the value has moved away from the threshold by the configured amount **in the deactivating direction**. Activation itself still happens exactly at the threshold — the gap is one-sided, like an ordinary thermostat. While the value sits inside that gap (was already active, but just below the nominal threshold), the linear output is pinned at `output_low` rather than drifting negative.

Hysteresis and the activation delay solve different kinds of problems and work well together: hysteresis is a gap **in value**, the delay is a gap **in time**. If the real source of instability is the averaging sensor's own drift (`sensor.average_humidity` and the like), it's also worth widening that sensor's own averaging window or smoothing — hysteresis on the rule softens the symptom, but does not replace smoothing at the source if there isn't any.

## Two execution contours

**Contour 1: instant calculation (event-driven).** `async_track_state_change_event` tracks every rule sensor, every fan, the presence sensor, and the `input_datetime` entities. Any change immediately recalculates the targets and updates the master entity's attributes. **No commands are sent to the fans.**

**Contour 2: throttled execution (timer-driven).** On the configured interval, via `async_track_time_interval`, the controller:

1. recalculates the targets (to account for a day/night transition that happened purely due to the clock);
2. compares the fans' current state to the targets;
3. applies a **gradual ramp**: no more than ±1 step or ±5% per tick;
4. dispatches `fan.set_preset_mode`, `fan.set_percentage`, or `fan.turn_off`.

The order of commands within a tick protects positive pressure: raising intake and lowering exhaust go first, everything else follows.

Step-to-preset mapping: if a fan's `preset_modes` count equals the number of steps excluding zero, step *N* maps to `preset_modes[N-1]`, and step 0 is `turn_off`. Otherwise the step is converted to a percentage (`N * 100 / step count`).

## Read-only fans

A fan can be marked **autonomous** (read-only) — for example, if it's controlled by its own device logic or by someone else's automation. For such a fan:

- the balancer **never sends it commands** — not in normal operation, not during a forced correction either;
- its current real performance (airflow computed from its actual state) **still counts towards the overall balance** exactly like a controllable fan — the other fans in the same role adjust around it;
- in the calculations its target always equals its own current actual level (floor = ceiling = demand = actual) — so it never ends up on any "raise"/"lower"/"trim" list during balancing;
- the wizard's "Day/night ceiling" and "Sensor rules" steps are skipped for it — there's nothing to control;
- if its state is unavailable, it is excluded from the balance like any other unavailable fan (its airflow counts as zero).

## Protection against prolonged and excessive imbalance

The balancer's primary goal is to keep intake and exhaust at roughly ±0 of each other, with a small acceptable excess of intake over exhaust (never the other way round). Independent fans physically ramp at different speeds (steps move by 1 per tick, percentages by 5% per tick, and non-linearly along the airflow map on top of that), so even with a perfectly computed target, a naive independent gradual ramp on each side can let actual intake and actual exhaust drift apart for several minutes, in either direction. To prevent that, exhaust fan execution is **synchronized to the real, current intake**, not only to its own final target.

### Exhaust's pace is tied to actual intake

On every tick, before deciding what to do with the exhaust fans, the controller builds a corridor `[actual_intake − tolerance, actual_intake + tolerance]` (tolerance = `pressure_tolerance`, 15 m³/h by default) and clamps the **combined** target airflow of every exhaust fan into it — if the final target is above the corridor it's capped down; if below, it's raised up. With several exhaust fans, the correction is distributed between them proportionally to their own targets. The resulting value (`goal_level`) becomes the reference exhaust chases this tick — the ordinary gradual ramp (±5%/±1 step per tick) moves towards it, not towards the final target.

This works symmetrically in both directions:
- **Exhaust above intake** (e.g. a presence sensor just fired and pushed demand to 100%, while intake has only just started ramping up from step 2) — the corridor won't let exhaust jump to its distant final target until intake has physically caught up; exhaust will instead climb **following actual** intake, recomputing the corridor fresh every tick, and only reach its true target once intake does too.
- **Exhaust below intake** (intake hasn't yet come down to its new, lower target) — the corridor won't let exhaust collapse far below, overtaking intake, which would just swap the eliminated negative imbalance for an equally unwanted positive one.

### Immediate correction once the tolerance is exceeded

The ordinary gradual ramp is enough as long as the corridor and the actual values haven't diverged much. But if the **actual** difference between intake and exhaust already exceeds `pressure_tolerance` in magnitude, the exhaust fans are moved straight to `goal_level` (already bounded by the same corridor, see above) within that same tick, bypassing the normal gradual step, instead of crawling towards it over several ticks at ±5%. Because the target is always `goal_level`, never the raw final target, this can never overshoot into the opposite imbalance — it only removes an unnecessary delay. The event is logged at `WARNING` level.

### Stuck-fan watchdog

Independently of pressure, the controller tracks each controllable fan's **actual** level between ticks and compares it against the **current reference** (`goal_level`) — for exhaust that's already the corridor-adjusted value, not the distant final target, otherwise exhaust's own intentional lag behind its target (see above) would look like it's stuck. If the level **doesn't move at all** while it still clearly differs from the reference, a timer starts. A fan that genuinely keeps moving towards it (even if it needs many ticks because of a large initial gap) never trips this timer — it resets on any real level change. But if the actual level has been frozen in place for longer than the **max correction time** (`max_correction_seconds`, 180s by default), the fan is forced straight to the current reference (`goal_level`), bypassing the gradual ramp, with a warning logged.

This guards against a scenario where something (a `preset_modes` count mismatch, a slow device response, a missed state event, and so on) silently blocks the ordinary step-by-step commands: without this, a fan could sit at a stale level for hours despite a completely different target; now the mismatch is guaranteed to be resolved within `max_correction_seconds` — and, thanks to using `goal_level`, resolved without risking an overshoot on the other side.

Both mechanisms are visible in the master entity's and the target-flow sensors' attributes: `stuck_seconds` — how long a fan has been "stuck" (`null` if it's converging normally).

## Balancer math

1. **Each fan's demand** = `max(presence minimum, rules)`, capped at the night ceiling.
2. **Airflow**: steps are read straight from the airflow map; percentage is `Current_Airflow = (Current_Percentage / 100) * Max_Airflow_Capacity`.
3. **Positive pressure.** While combined intake is below combined exhaust, intake fans are raised one step at a time (+1 step or +5%) within their ceilings; the least-loaded one goes first.
4. **Protection.** If intake has hit its ceilings, exhaust is reduced to match it. The `exhaust_throttled_for_positive_pressure` note appears in the attributes.
5. **Parity.** Exhaust fans are matched to the current intake: large discrete steps that still fit the remaining gap first, then smooth percentage fans for fine-tuning.
6. **Trimming.** Any intake surplus above a fan's own demand is trimmed back, as long as "intake ≥ exhaust" still holds.

The target delta (`target_delta_m3h`) converges towards 0. An exact zero is reachable whenever at least one percentage-controlled fan exists in the needed role. With discrete steps only, some residual delta is unavoidable, and positive pressure always takes priority (`delta ≥ 0`). If intake minimums or rules exceed what exhaust can use, the delta stays positive and the `residual_positive_delta` note appears.

## Fault tolerance

- An unavailable fan is excluded from the balance (its airflow counts as zero); the rest keep working, and exhaust is capped by actual intake in the meantime.
- An unavailable sensor is replaced by its rule's own policy; the same policy applies if a sensor used as a dynamic threshold is unavailable.
- An unavailable presence sensor is treated as "home"; unavailable night-window boundaries are treated as "not night".
- A calculation or command error is logged and does not crash Home Assistant; the previous plan is kept.
- A profile with errors is skipped at load time with a log message; if no valid fan is left at all, integration setup fails outright (`ConfigEntryError`) instead of silently starting with nothing to balance.
- A read-only fan never receives commands; if it becomes unavailable it is simply excluded from the balance, like any other fan.
- The stuck-fan watchdog and the immediate pressure correction (see above) guarantee that a significant gap between the actual and target level never persists uncontrolled.

## Master entity

`fan.vav_balancer` (one per integration):

- **on** — the controller sends commands to the fans;
- **off** — calculation only (Contour 1); Contour 2 sends nothing. The state is restored after a restart.

Main attributes: `target_intake_m3h`, `target_exhaust_m3h`, `requested_exhaust_m3h`, `target_delta_m3h`, `actual_intake_m3h`, `actual_exhaust_m3h`, `actual_delta_m3h`, `night_mode`, `home_mode`, `pressure_tolerance_m3h`, `max_correction_seconds`, `intake_targets`, `exhaust_targets` (level, airflow, demand, ceiling, `read_only`, `stuck_seconds`, **and the list of rules with their current thresholds**, per fan), `notes`, `last_reason`, `last_calculation`, `last_execution`, `last_commands`.

## Dashboard entities

Besides the master entity, the integration creates separate sensors — this gives you history and statistics graphs through Home Assistant's own tooling, with no third-party cards needed.

**Global sensors (m³/h), each with `device_class: volume_flow_rate` and `state_class: measurement`:**

| Sensor | Value |
|---|---|
| `sensor.vav_balancer_target_intake_flow` | Target combined intake |
| `sensor.vav_balancer_actual_intake_flow` | Actual combined intake |
| `sensor.vav_balancer_target_exhaust_flow` | Target combined exhaust |
| `sensor.vav_balancer_actual_exhaust_flow` | Actual combined exhaust |
| `sensor.vav_balancer_requested_exhaust_flow` | Requested exhaust before the positive-pressure cap (disabled by default in the entity registry; diagnostic category) |
| `sensor.vav_balancer_target_delta` | Target intake-minus-exhaust delta |
| `sensor.vav_balancer_actual_delta` | Actual intake-minus-exhaust delta |

**Per configured fan** (the entity's name is built from its `object_id`, i.e. the part after the dot in `fan.xxx`):

- `sensor.vav_balancer_target_flow_<object_id>` — target airflow, m³/h. Attributes: `control`, `target_level`, `demand_level`, `ceiling`, `available`, `read_only`, `stuck_seconds`, and `rules` — that fan's rules, each with `sensor`, `mode`, `threshold`, `threshold_source` (`fixed` or the threshold sensor's entity_id), `threshold_high`, `threshold_high_source`, `output`, `active`, `delay_seconds`, `hysteresis`, `pending` (the raw state hasn't been confirmed by the activation delay yet).
- `sensor.vav_balancer_actual_flow_<object_id>` — actual airflow, m³/h, computed from that fan's current state.

Both sensors become `unavailable` (not "0", not "unknown") when there is nothing meaningful to report — e.g. before the first calculation (target) or while the fan's own state can't be read (actual).

**Binary sensors** (diagnostic category):

- `binary_sensor.vav_balancer_night_mode` — whether the night ceiling currently applies;
- `binary_sensor.vav_balancer_home_mode` — whether the "home" or "away" minimum currently applies.

Exact `entity_id`s depend on how Home Assistant resolves name collisions — check them under **Developer tools → States**, filtering by `vav_balancer`.

### Dashboard example

`dashboard_example.yaml` is included: a manual-mode Lovelace YAML view with a `history-graph` card (target/actual intake and exhaust, pressure delta), a `glance` card for the modes, and a `markdown` card that reads a target sensor's `rules` attribute through Jinja and builds a "sensor → threshold → source → output → active" table for one fan. Before using it, replace the `<object_id>` placeholder with your fans' real `entity_id` parts.

## Logging and diagnostics

All messages are in English. For detailed tracing (config validation, every balancer phase, every tick and command):

```yaml
logger:
  default: warning
  logs:
    custom_components.vav_balancer: debug
```

For a one-off state snapshot (to attach to a bug report), use **Settings → Devices & services → VAV Ventilation Balancer → Download diagnostics** — the file contains the current stored configuration and a full snapshot of the controller's calculations (targets, actual airflow, active rules, warnings). The configuration contains no credentials — only entity ids and numeric settings.
