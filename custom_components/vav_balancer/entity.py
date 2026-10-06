"""Shared base entity for the VAV Balancer integration.

Every entity across fan/sensor/binary_sensor follows the same pattern: no
polling, grouped under the integration's single device, and pushed a state
update whenever the controller recalculates. Factoring that out here (per
the quality scale's common-modules rule) keeps the platform modules focused
on what makes each entity different.
"""
from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DEFAULT_NAME, DOMAIN
from .controller import VAVController


class VAVEntity(Entity):
    """Common base: no polling, shared device, pushed controller updates."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, controller: VAVController, entry_id: str) -> None:
        self._controller = controller
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry_id)}, name=DEFAULT_NAME
        )

    async def async_added_to_hass(self) -> None:
        """Register for controller updates once added to hass."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._controller.async_add_listener(self._handle_controller_update)
        )

    @callback
    def _handle_controller_update(self) -> None:
        """Push the latest controller state to Home Assistant."""
        self.async_write_ha_state()
