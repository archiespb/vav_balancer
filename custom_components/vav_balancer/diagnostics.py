"""Diagnostics support for the VAV Ventilation Balancer.

Nothing stored by this integration is sensitive (entity ids and numeric
tuning values only, no credentials or tokens), so nothing is redacted.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from . import VAVConfigEntry


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: "VAVConfigEntry"
) -> dict[str, Any]:
    """Return the stored configuration plus the controller's live state."""
    controller = entry.runtime_data
    return {
        "config": dict(entry.options) if entry.options else dict(entry.data),
        "controller_state": controller.state_attributes(),
    }
