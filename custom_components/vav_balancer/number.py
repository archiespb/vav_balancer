"""Number entities for live-tuning the balancer's safety parameters.

Changing one of these updates the config entry's options (the same place
the wizard's "global variables" step writes to), so Home Assistant reloads
the integration to apply it -- the new value then persists across restarts
exactly like any other wizard-set value, instead of living only in memory.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTime, UnitOfVolumeFlowRate
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    CONF_MAX_CORRECTION_SECONDS,
    CONF_PRESSURE_TOLERANCE,
    MAX_CORRECTION_SECONDS_LIMIT,
    MAX_PRESSURE_TOLERANCE,
    MIN_CORRECTION_SECONDS,
    MIN_PRESSURE_TOLERANCE,
)
from .entity import VAVEntity

if TYPE_CHECKING:
    from . import VAVConfigEntry
    from .controller import VAVController

_LOGGER = logging.getLogger(__name__)

# No polling: the current value is read straight from the controller.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class VAVNumberDescription(NumberEntityDescription):
    """Describes one live-tunable parameter and where it's stored."""

    config_key: str
    get_fn: Callable[["VAVController"], float]


DESCRIPTIONS: tuple[VAVNumberDescription, ...] = (
    VAVNumberDescription(
        key="pressure_tolerance",
        translation_key="pressure_tolerance",
        icon="mdi:gauge",
        native_min_value=MIN_PRESSURE_TOLERANCE,
        native_max_value=MAX_PRESSURE_TOLERANCE,
        native_step=1,
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        config_key=CONF_PRESSURE_TOLERANCE,
        get_fn=lambda c: c.pressure_tolerance,
    ),
    VAVNumberDescription(
        key="max_correction_seconds",
        translation_key="max_correction_seconds",
        icon="mdi:timer-alert",
        native_min_value=MIN_CORRECTION_SECONDS,
        native_max_value=MAX_CORRECTION_SECONDS_LIMIT,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        config_key=CONF_MAX_CORRECTION_SECONDS,
        get_fn=lambda c: c.max_correction_seconds,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: "VAVConfigEntry", async_add_entities: AddEntitiesCallback
) -> None:
    """Create the tuning number entities."""
    controller = entry.runtime_data
    async_add_entities(
        VAVTuningNumber(controller, entry, description) for description in DESCRIPTIONS
    )


class VAVTuningNumber(VAVEntity, NumberEntity):
    """A persisted, live-adjustable safety-tuning parameter."""

    def __init__(
        self, controller: "VAVController", entry: "VAVConfigEntry",
        description: VAVNumberDescription,
    ) -> None:
        super().__init__(controller, entry.entry_id)
        self._entry = entry
        self.entity_description: VAVNumberDescription = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"

    @property
    def native_value(self) -> float:
        return self.entity_description.get_fn(self._controller)

    async def async_set_native_value(self, value: float) -> None:
        # async_update_entry is synchronous despite its name; it schedules
        # the registered update listener (which reloads this entry) to run.
        options: dict[str, Any] = (
            dict(self._entry.options) if self._entry.options else dict(self._entry.data)
        )
        options[self.entity_description.config_key] = value
        _LOGGER.info(
            "%s changed to %s via the number entity; reloading to apply it",
            self.entity_description.config_key, value,
        )
        self.hass.config_entries.async_update_entry(self._entry, options=options)
