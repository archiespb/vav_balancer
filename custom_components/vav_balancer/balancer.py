"""Pure balancing math for the VAV balancer (no Home Assistant I/O).

Phases
------
A. Start from per-fan demand (rules, occupancy minimum, night ceiling).
B. Positive-pressure cruise control: ramp intake fans up until
   intake >= exhaust demand (or every intake fan is at its ceiling).
C. If intake is capped below exhaust demand, throttle exhaust to fit intake.
D. Parity: raise exhaust fans (discrete first, then continuous fine tuning)
   so that exhaust converges to the intake airflow.
E. Trim surplus intake (only the units that were ramped above their own
   demand) so delta converges to 0 m3/h where hardware granularity allows.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .const import EPSILON, FIT_TOLERANCE, MAX_ITERATIONS
from .models import FanModel

_LOGGER = logging.getLogger(__name__)


@dataclass
class FanInput:
    """Everything the balancer needs to know about one fan."""

    model: FanModel
    available: bool
    rule_level: float | None  # highest level demanded by active rules
    floor: float  # occupancy minimum (already clamped to ceiling)
    ceiling: float  # night-mode / hardware ceiling
    demand: float  # max(floor, rule_level) clamped to ceiling


@dataclass
class FanTarget:
    """Calculated ideal target of one fan."""

    entity_id: str
    role: str
    control: str
    level: float
    flow: float
    demand_level: float
    ceiling: float
    available: bool


@dataclass
class BalancePlan:
    """Result of a balancing run."""

    intake: dict[str, FanTarget] = field(default_factory=dict)
    exhaust: dict[str, FanTarget] = field(default_factory=dict)
    intake_flow: float = 0.0
    exhaust_flow: float = 0.0
    exhaust_demand_flow: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def delta(self) -> float:
        """Target intake minus target exhaust (m3/h). Converges to 0."""
        return self.intake_flow - self.exhaust_flow


def balance(intakes: list[FanInput], exhausts: list[FanInput]) -> BalancePlan:
    """Compute target levels for every intake and exhaust fan."""
    notes: list[str] = []
    imap = {i.model.entity_id: i for i in intakes}
    emap = {e.model.entity_id: e for e in exhausts}

    # Phase A -------------------------------------------------------------
    ilv = {k: (v.demand if v.available else 0.0) for k, v in imap.items()}
    elv = {k: (v.demand if v.available else 0.0) for k, v in emap.items()}
    for eid, inp in {**imap, **emap}.items():
        if not inp.available:
            notes.append(f"unavailable:{eid}")

    def iflow() -> float:
        return sum(imap[k].model.flow(v) for k, v in ilv.items())

    def eflow() -> float:
        return sum(emap[k].model.flow(v) for k, v in elv.items())

    def load(inp: FanInput, level: float) -> float:
        return level / max(inp.model.max_level, 1.0)

    exhaust_demand_flow = eflow()
    _LOGGER.debug(
        "Balancer phase A: intake=%.1f m3/h exhaust demand=%.1f m3/h",
        iflow(), exhaust_demand_flow,
    )

    # Phase B: raise intake until it covers the exhaust demand -------------
    for _ in range(MAX_ITERATIONS):
        if iflow() >= eflow() - EPSILON:
            break
        candidates = [
            k for k, v in imap.items() if v.available and ilv[k] < v.ceiling - 1e-9
        ]
        if not candidates:
            notes.append("intake_capped")
            _LOGGER.debug("Balancer phase B: all intake fans are at their ceiling")
            break
        key = min(candidates, key=lambda k: load(imap[k], ilv[k]))
        inp = imap[key]
        new_level = min(ilv[key] + inp.model.increment, inp.ceiling)
        _LOGGER.debug(
            "Balancer phase B: intake %s %.1f -> %.1f", key, ilv[key], new_level
        )
        ilv[key] = inp.model.clamp(new_level, inp.ceiling)

    # Phase C: throttle exhaust when intake cannot keep up -----------------
    throttled = False
    for _ in range(MAX_ITERATIONS):
        excess = eflow() - iflow()
        if excess <= EPSILON:
            break
        candidates = [k for k, v in elv.items() if v > 0]
        if not candidates:
            break
        key = max(candidates, key=lambda k: emap[k].model.flow(elv[k]))
        model = emap[key].model
        throttled = True
        if model.is_steps:
            new_level = max(elv[key] - 1.0, 0.0)
        else:
            cut = min(excess, model.flow(elv[key]))
            new_level = max(elv[key] - cut * model.level_per_flow(), 0.0)
        _LOGGER.debug(
            "Balancer phase C: exhaust %s %.1f -> %.1f (excess %.1f m3/h)",
            key, elv[key], new_level, excess,
        )
        elv[key] = new_level
    if throttled:
        notes.append("exhaust_throttled_for_positive_pressure")

    # Phase D helper: raise exhaust towards intake -------------------------
    def raise_exhaust() -> bool:
        changed = False
        for _ in range(MAX_ITERATIONS):
            remaining = iflow() - eflow()
            if remaining <= EPSILON:
                break
            choice: tuple[str, float] | None = None

            # Discrete fans first: biggest step that still fits.
            best_gain = 0.0
            for key, inp in emap.items():
                model = inp.model
                if not inp.available or not model.is_steps:
                    continue
                cur = int(round(elv[key]))
                base = model.flow(cur)
                for nxt in range(cur + 1, int(inp.ceiling) + 1):
                    gain = model.flow(nxt) - base
                    if gain <= FIT_TOLERANCE:
                        continue  # plateau in the map, look further
                    if gain <= remaining + FIT_TOLERANCE and gain > best_gain:
                        best_gain = gain
                        choice = (key, float(nxt))
                    break  # first positive step decides for this fan

            # Continuous fans fine-tune what is left.
            if choice is None:
                candidates = [
                    k for k, v in emap.items()
                    if v.available and not v.model.is_steps and elv[k] < v.ceiling - 1e-9
                ]
                if candidates:
                    key = min(candidates, key=lambda k: load(emap[k], elv[k]))
                    inp = emap[key]
                    model = inp.model
                    gain_max = model.flow(inp.ceiling) - model.flow(elv[key])
                    gain = min(remaining, gain_max)
                    if gain > FIT_TOLERANCE:
                        new_level = min(
                            elv[key] + gain * model.level_per_flow(), inp.ceiling
                        )
                        choice = (key, new_level)

            if choice is None:
                break
            _LOGGER.debug(
                "Balancer phase D: exhaust %s %.2f -> %.2f (remaining %.1f m3/h)",
                choice[0], elv[choice[0]], choice[1], remaining,
            )
            elv[choice[0]] = choice[1]
            changed = True
        return changed

    # Phase E helper: trim surplus intake ----------------------------------
    def trim_intake() -> bool:
        changed = False
        for _ in range(MAX_ITERATIONS):
            remaining = iflow() - eflow()
            if remaining <= EPSILON:
                break
            choice: tuple[str, float] | None = None

            best_loss = 0.0
            for key, inp in imap.items():
                model = inp.model
                if not inp.available or not model.is_steps:
                    continue
                if ilv[key] <= inp.demand + 1e-9:
                    continue
                cur = int(round(ilv[key]))
                loss = model.flow(cur) - model.flow(cur - 1)
                if FIT_TOLERANCE < loss <= remaining + FIT_TOLERANCE and loss > best_loss:
                    best_loss = loss
                    choice = (key, float(cur - 1))

            if choice is None:
                for key, inp in imap.items():
                    model = inp.model
                    if not inp.available or model.is_steps:
                        continue
                    if ilv[key] <= inp.demand + 1e-9:
                        continue
                    loss_max = model.flow(ilv[key]) - model.flow(inp.demand)
                    loss = min(remaining, loss_max)
                    if loss > FIT_TOLERANCE:
                        choice = (
                            key,
                            max(ilv[key] - loss * model.level_per_flow(), inp.demand),
                        )
                        break

            if choice is None:
                break
            _LOGGER.debug(
                "Balancer phase E: intake %s %.2f -> %.2f (surplus %.1f m3/h)",
                choice[0], ilv[choice[0]], choice[1], remaining,
            )
            ilv[choice[0]] = choice[1]
            changed = True
        return changed

    # Phases D/E: alternate until nothing changes ---------------------------
    for round_no in range(20):
        changed_d = raise_exhaust()
        changed_e = trim_intake()
        _LOGGER.debug(
            "Balancer parity round %d: exhaust_changed=%s intake_changed=%s delta=%.2f",
            round_no, changed_d, changed_e, iflow() - eflow(),
        )
        if not (changed_d or changed_e):
            break

    # Build the plan --------------------------------------------------------
    plan = BalancePlan(notes=notes)
    for key, inp in imap.items():
        level = round(ilv[key], 2)
        plan.intake[key] = FanTarget(
            key, inp.model.role, inp.model.control, level,
            inp.model.flow(level), inp.demand, inp.ceiling, inp.available,
        )
    for key, inp in emap.items():
        level = round(elv[key], 2)
        plan.exhaust[key] = FanTarget(
            key, inp.model.role, inp.model.control, level,
            inp.model.flow(level), inp.demand, inp.ceiling, inp.available,
        )
    plan.intake_flow = sum(t.flow for t in plan.intake.values())
    plan.exhaust_flow = sum(t.flow for t in plan.exhaust.values())
    plan.exhaust_demand_flow = exhaust_demand_flow

    if plan.delta > EPSILON:
        notes.append("residual_positive_delta")
    elif plan.delta < -EPSILON:
        notes.append("negative_pressure_delta")

    _LOGGER.debug(
        "Balancer result: intake=%.1f exhaust=%.1f delta=%.2f notes=%s",
        plan.intake_flow, plan.exhaust_flow, plan.delta, notes,
    )
    return plan
