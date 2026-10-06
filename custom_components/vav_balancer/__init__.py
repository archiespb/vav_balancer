"""VAV Ventilation Balancer: agnostic multi-step ventilation balancing."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError

from .const import PLATFORMS
from .controller import VAVController

_LOGGER = logging.getLogger(__name__)

# ConfigEntry typed with the runtime object it carries (quality scale:
# runtime-data). Used throughout this integration instead of hass.data.
type VAVConfigEntry = ConfigEntry[VAVController]


async def async_setup_entry(hass: HomeAssistant, entry: VAVConfigEntry) -> bool:
    """Set up the balancer from a config entry."""
    _LOGGER.info("Setting up VAV balancer entry %s", entry.entry_id)
    controller = VAVController(hass, entry)

    # quality scale: test-before-setup -- a config entry with zero loaded
    # fans (every profile failed validation, or none were ever stored) is
    # not a usable integration; fail setup clearly instead of silently
    # coming up with nothing to balance.
    if not controller.intake_models and not controller.exhaust_models:
        raise ConfigEntryError(
            "No valid intake or exhaust fan profile could be loaded from this "
            "config entry. Reconfigure the integration."
        )

    entry.runtime_data = controller

    # Entities register their listeners first, then the trackers start,
    # so the very first calculation is already published.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await controller.async_start()

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: VAVConfigEntry) -> bool:
    """Unload the entry and stop both execution contours."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
        _LOGGER.info("VAV balancer entry %s unloaded", entry.entry_id)
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: VAVConfigEntry) -> None:
    """Reload when options change."""
    _LOGGER.debug("Options changed, reloading entry %s", entry.entry_id)
    await hass.config_entries.async_reload(entry.entry_id)
