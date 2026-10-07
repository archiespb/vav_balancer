"""VAV Ventilation Balancer: agnostic multi-step ventilation balancing."""
from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryError

from .const import DOMAIN, MAX_BOOST_MINUTES, MIN_BOOST_MINUTES, PLATFORMS
from .controller import VAVController

_LOGGER = logging.getLogger(__name__)

# ConfigEntry typed with the runtime object it carries (quality scale:
# runtime-data). Used throughout this integration instead of hass.data.
type VAVConfigEntry = ConfigEntry[VAVController]

_CARD_URL_PATH = f"/{DOMAIN}/vav-balancer-card.js"
_CARD_FILE = Path(__file__).parent / "www" / "vav-balancer-card.js"
# Registering a static path / extra JS module is a process-wide action, not
# per-entry state -- a tiny hass.data flag here is the right tool (unlike
# the per-entry controller, which lives on entry.runtime_data), since it
# just guards against re-registering the same aiohttp route on every reload.
_FRONTEND_REGISTERED = f"{DOMAIN}_frontend_registered"

SERVICE_BOOST = "boost"
SERVICE_CANCEL_BOOST = "cancel_boost"
ATTR_DURATION = "duration"
BOOST_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_DURATION): vol.All(
            vol.Coerce(float), vol.Range(min=MIN_BOOST_MINUTES, max=MAX_BOOST_MINUTES)
        ),
    }
)


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
    await _async_register_services(hass, entry)
    await _async_register_frontend_card(hass)
    return True


async def _async_register_frontend_card(hass: HomeAssistant) -> None:
    """Serve www/vav-balancer-card.js and register it as a Lovelace
    resource, so the custom card works without the user having to add a
    resource manually. Runs at most once per Home Assistant run.

    Deliberately not a hard manifest dependency on "frontend": some
    instances (headless/API-only, or minimal test harnesses) don't load
    it, and the integration itself works completely fine without the
    card -- it's a convenience, not core functionality. If frontend isn't
    present, this just logs and skips registering it.
    """
    if hass.data.get(_FRONTEND_REGISTERED):
        return
    if "frontend" not in hass.config.components or not hasattr(hass, "http"):
        _LOGGER.debug(
            "frontend is not loaded; skipping the bundled Lovelace card "
            "(the integration itself is unaffected)"
        )
        return
    hass.data[_FRONTEND_REGISTERED] = True
    try:
        await hass.http.async_register_static_paths(
            [StaticPathConfig(_CARD_URL_PATH, str(_CARD_FILE), cache_headers=False)]
        )
        add_extra_js_url(hass, _CARD_URL_PATH)
    except Exception:  # noqa: BLE001 - the card is optional, never fatal
        _LOGGER.exception("Could not register the bundled Lovelace card")
        return
    _LOGGER.debug("Registered frontend card at %s", _CARD_URL_PATH)


async def _async_register_services(hass: HomeAssistant, entry: VAVConfigEntry) -> None:
    """Register the (single-instance) vav_balancer.* services, bound to
    this entry's controller. Removed again on unload so a reload re-binds
    them to the fresh controller instead of calling into a stale one."""

    async def _handle_boost(call: ServiceCall) -> None:
        duration = call.data.get(ATTR_DURATION)
        entry.runtime_data.async_start_boost(duration)

    async def _handle_cancel_boost(call: ServiceCall) -> None:
        entry.runtime_data.async_cancel_boost()

    hass.services.async_register(DOMAIN, SERVICE_BOOST, _handle_boost, schema=BOOST_SCHEMA)
    hass.services.async_register(DOMAIN, SERVICE_CANCEL_BOOST, _handle_cancel_boost)
    entry.async_on_unload(lambda: hass.services.async_remove(DOMAIN, SERVICE_BOOST))
    entry.async_on_unload(lambda: hass.services.async_remove(DOMAIN, SERVICE_CANCEL_BOOST))


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
