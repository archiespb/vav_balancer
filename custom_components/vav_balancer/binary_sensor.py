"""Binary sensors exposing night/home mode, for dashboards and history graphs."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import VAVEntity

if TYPE_CHECKING:
    from . import VAVConfigEntry
    from .controller import VAVController

# No polling: both entities are pushed updates by the controller.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class VAVBinarySensorDescription(BinarySensorEntityDescription):
    """Describes one mode binary sensor."""

    value_fn: Callable[["VAVController"], bool]


DESCRIPTIONS: tuple[VAVBinarySensorDescription, ...] = (
    VAVBinarySensorDescription(
        key="night_mode",
        translation_key="night_mode",
        icon="mdi:weather-night",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.night,
    ),
    VAVBinarySensorDescription(
        key="home_mode",
        translation_key="home_mode",
        device_class=BinarySensorDeviceClass.PRESENCE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda c: c.home,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: "VAVConfigEntry", async_add_entities: AddEntitiesCallback
) -> None:
    """Create the night-mode and home-mode binary sensors."""
    controller = entry.runtime_data
    async_add_entities(
        VAVModeBinarySensor(controller, entry.entry_id, description)
        for description in DESCRIPTIONS
    )


class VAVModeBinarySensor(VAVEntity, BinarySensorEntity):
    """Night mode / home mode, as currently applied by the controller."""

    def __init__(
        self, controller: "VAVController", entry_id: str,
        description: VAVBinarySensorDescription,
    ) -> None:
        super().__init__(controller, entry_id)
        self.entity_description: VAVBinarySensorDescription = description
        self._attr_unique_id = f"{entry_id}_{description.key}"

    @property
    def is_on(self) -> bool:
        return self.entity_description.value_fn(self._controller)
