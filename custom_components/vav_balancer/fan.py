"""Master FanEntity exposing the VAV balancer state."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import VAVEntity

if TYPE_CHECKING:
    from . import VAVConfigEntry
    from .controller import VAVController

_LOGGER = logging.getLogger(__name__)

# Single entity, never polled -- no need to allow concurrent updates.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: "VAVConfigEntry", async_add_entities: AddEntitiesCallback
) -> None:
    """Create the single master entity."""
    async_add_entities([VAVMasterFan(entry.runtime_data, entry.entry_id)])


class VAVMasterFan(RestoreEntity, VAVEntity, FanEntity):
    """On = balancer actively drives the fans; off = calculation only."""

    _attr_name = None
    # _attr_name = None already fixes the entity's name to the device name
    # (see Entity._name_internal: _attr_name is checked before
    # translation_key), so this translation_key only drives icons.json --
    # it has no effect on naming.
    _attr_translation_key = "balancer"
    _attr_icon = "mdi:fan-auto"
    _attr_supported_features = FanEntityFeature.TURN_ON | FanEntityFeature.TURN_OFF

    def __init__(self, controller: "VAVController", entry_id: str) -> None:
        super().__init__(controller, entry_id)
        self._attr_unique_id = f"{entry_id}_master"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state == STATE_OFF:
            _LOGGER.debug("Restoring master entity state: OFF")
            self._controller.set_enabled(False)

    @property
    def available(self) -> bool:
        # The master entity always reflects the controller's own state
        # (enabled flag, last plan), which is meaningful even before the
        # first calculation or if every fan is unavailable -- unlike the
        # per-fan sensors, there is no "no data" case here.
        return True

    @property
    def is_on(self) -> bool:
        return self._controller.enabled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._controller.state_attributes()

    async def async_turn_on(
        self, percentage: int | None = None, preset_mode: str | None = None, **kwargs: Any
    ) -> None:
        self._controller.set_enabled(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._controller.set_enabled(False)
