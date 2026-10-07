"""Button entities for the VAV Balancer integration."""
from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import VAVEntity

if TYPE_CHECKING:
    from . import VAVConfigEntry
    from .controller import VAVController

# No polling: this entity only ever reacts to being pressed.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: "VAVConfigEntry", async_add_entities: AddEntitiesCallback
) -> None:
    """Create the boost button."""
    async_add_entities([VAVBoostButton(entry.runtime_data, entry.entry_id)])


class VAVBoostButton(VAVEntity, ButtonEntity):
    """Force every controllable fan to its ceiling for the configured
    default duration (see the "Boost duration" field in the wizard, or
    the vav_balancer.boost service for a one-off override)."""

    _attr_translation_key = "boost"
    _attr_icon = "mdi:fan-plus"

    def __init__(self, controller: "VAVController", entry_id: str) -> None:
        super().__init__(controller, entry_id)
        self._attr_unique_id = f"{entry_id}_boost"

    async def async_press(self) -> None:
        self._controller.async_start_boost()
