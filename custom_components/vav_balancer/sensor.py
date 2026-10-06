"""Sensor entities exposing balancer performance for dashboards and graphs.

Two families of sensors are created:

- Global metrics: target/actual intake and exhaust airflow, the requested
  exhaust demand, and the target/actual pressure delta. These are static
  (translated) entities, good for a single history/statistics graph.
- Per-fan flow: target and actual airflow (m3/h) for every configured
  fan, with the fan's rule evaluation (sensor, threshold, output, active)
  exposed as attributes for a markdown/template dashboard card.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfVolumeFlowRate
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import VAVEntity

if TYPE_CHECKING:
    from . import VAVConfigEntry
    from .controller import VAVController

_LOGGER = logging.getLogger(__name__)

# No polling: every entity here is pushed updates by the controller.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class VAVGlobalSensorDescription(SensorEntityDescription):
    """Describes one global metric sensor."""

    value_fn: Callable[["VAVController"], float | None]


GLOBAL_DESCRIPTIONS: tuple[VAVGlobalSensorDescription, ...] = (
    VAVGlobalSensorDescription(
        key="target_intake_flow",
        translation_key="target_intake_flow",
        icon="mdi:fan-chevron-up",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda c: c.global_metrics()["target_intake_flow"],
    ),
    VAVGlobalSensorDescription(
        key="actual_intake_flow",
        translation_key="actual_intake_flow",
        icon="mdi:fan",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda c: c.global_metrics()["actual_intake_flow"],
    ),
    VAVGlobalSensorDescription(
        key="target_exhaust_flow",
        translation_key="target_exhaust_flow",
        icon="mdi:fan-chevron-down",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda c: c.global_metrics()["target_exhaust_flow"],
    ),
    VAVGlobalSensorDescription(
        key="actual_exhaust_flow",
        translation_key="actual_exhaust_flow",
        icon="mdi:fan",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda c: c.global_metrics()["actual_exhaust_flow"],
    ),
    VAVGlobalSensorDescription(
        key="requested_exhaust_flow",
        translation_key="requested_exhaust_flow",
        icon="mdi:fan-alert",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda c: c.global_metrics()["requested_exhaust_flow"],
    ),
    VAVGlobalSensorDescription(
        key="target_delta",
        translation_key="target_delta",
        icon="mdi:gauge",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda c: c.global_metrics()["target_delta"],
    ),
    VAVGlobalSensorDescription(
        key="actual_delta",
        translation_key="actual_delta",
        icon="mdi:gauge",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda c: c.global_metrics()["actual_delta"],
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: "VAVConfigEntry", async_add_entities: AddEntitiesCallback
) -> None:
    """Create global metric sensors plus two per fan (target + actual flow)."""
    controller = entry.runtime_data
    entities: list[SensorEntity] = [
        VAVGlobalSensor(controller, entry.entry_id, description)
        for description in GLOBAL_DESCRIPTIONS
    ]
    for model in (*controller.intake_models, *controller.exhaust_models):
        entities.append(
            VAVFanFlowSensor(
                controller, entry.entry_id, model.entity_id,
                target=True, read_only=model.read_only,
            )
        )
        entities.append(
            VAVFanFlowSensor(
                controller, entry.entry_id, model.entity_id,
                target=False, read_only=model.read_only,
            )
        )
    _LOGGER.debug("Sensor platform: creating %d entities", len(entities))
    async_add_entities(entities)


class VAVGlobalSensor(VAVEntity, SensorEntity):
    """One global target/actual airflow metric."""

    def __init__(
        self, controller: "VAVController", entry_id: str,
        description: VAVGlobalSensorDescription,
    ) -> None:
        super().__init__(controller, entry_id)
        self.entity_description: VAVGlobalSensorDescription = description
        self._attr_unique_id = f"{entry_id}_{description.key}"

    @property
    def available(self) -> bool:
        # quality scale: entity-unavailable -- e.g. the target_* sensors
        # have no meaningful value before the first calculation has run.
        return self.entity_description.value_fn(self._controller) is not None

    @property
    def native_value(self) -> float | None:
        value = self.entity_description.value_fn(self._controller)
        return None if value is None else round(value, 1)


class VAVFanFlowSensor(VAVEntity, SensorEntity):
    """Target or actual airflow (m3/h) of one configured fan."""

    _attr_device_class = SensorDeviceClass.VOLUME_FLOW_RATE
    _attr_native_unit_of_measurement = UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(
        self, controller: "VAVController", entry_id: str,
        fan_entity_id: str, *, target: bool, read_only: bool = False,
    ) -> None:
        super().__init__(controller, entry_id)
        self._fan_entity_id = fan_entity_id
        self._target = target
        self._read_only = read_only
        object_id = fan_entity_id.split(".", 1)[-1]
        # quality scale: entity-translations -- the fixed part of the name
        # ("Target flow" / "Actual flow") is translatable; the per-fan part
        # is a placeholder, since it has to stay dynamic (one entity per
        # configured fan).
        self._attr_translation_key = "fan_target_flow" if target else "fan_actual_flow"
        self._attr_translation_placeholders = {"fan_name": object_id}
        self._attr_icon = "mdi:fan-chevron-up" if target else "mdi:fan"
        self._attr_unique_id = (
            f"{entry_id}_{fan_entity_id}_{'target' if target else 'actual'}_flow"
        )

    def _raw_value(self) -> float | None:
        if self._target:
            tgt = self._controller.target_for(self._fan_entity_id)
            return None if tgt is None else tgt.flow
        return self._controller.actual_flow_for(self._fan_entity_id)

    @property
    def available(self) -> bool:
        # quality scale: entity-unavailable -- e.g. before the first
        # calculation (target) or while the fan's own state can't be read
        # (actual), there is nothing meaningful to report.
        return self._raw_value() is not None

    @property
    def native_value(self) -> float | None:
        value = self._raw_value()
        return None if value is None else round(value, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {
            "fan_entity_id": self._fan_entity_id,
            "read_only": self._read_only,
        }
        if not self._target:
            return attrs
        tgt = self._controller.target_for(self._fan_entity_id)
        if tgt is not None:
            attrs.update(
                {
                    "control": tgt.control,
                    "target_level": tgt.level,
                    "demand_level": tgt.demand_level,
                    "ceiling": tgt.ceiling,
                    "available": tgt.available,
                }
            )
        attrs["stuck_seconds"] = self._controller.stuck_seconds_for(self._fan_entity_id)
        attrs["rules"] = self._controller.rule_debug_for(self._fan_entity_id)
        return attrs
