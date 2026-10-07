"""Per-fan pause switches.

Distinct from the config-time "read-only" flag: this is a runtime,
reversible override (via the dashboard or an automation) that temporarily
excludes one fan from balancing without touching the stored configuration.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import VAVEntity

if TYPE_CHECKING:
    from . import VAVConfigEntry
    from .controller import VAVController

# No polling: state is read straight from the controller.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant, entry: "VAVConfigEntry", async_add_entities: AddEntitiesCallback
) -> None:
    """Create one pause switch per controllable (non-read-only) fan."""
    controller = entry.runtime_data
    entities = [
        VAVFanActiveSwitch(controller, entry.entry_id, model.entity_id)
        for model in (*controller.intake_models, *controller.exhaust_models)
        if not model.read_only
    ]
    async_add_entities(entities)


class VAVFanActiveSwitch(VAVEntity, SwitchEntity):
    """On: this fan is normally managed by the balancer.
    Off: paused -- never commanded, but its actual output still counts
    towards the balance, exactly like a read-only fan (see DOCS.md)."""

    _attr_icon = "mdi:fan-auto"

    def __init__(
        self, controller: "VAVController", entry_id: str, fan_entity_id: str
    ) -> None:
        super().__init__(controller, entry_id)
        self._fan_entity_id = fan_entity_id
        object_id = fan_entity_id.split(".", 1)[-1]
        self._attr_translation_key = "fan_active"
        self._attr_translation_placeholders = {"fan_name": object_id}
        self._attr_unique_id = f"{entry_id}_{fan_entity_id}_active"

    @property
    def is_on(self) -> bool:
        return not self._controller.is_paused(self._fan_entity_id)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"fan_entity_id": self._fan_entity_id}

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._controller.async_set_paused(self._fan_entity_id, False)

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._controller.async_set_paused(self._fan_entity_id, True)
