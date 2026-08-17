"""Sofar Inverter Modbus — built on modbus-connection.

Layering (see modbus-connection's integration guide): this module owns the
ModbusConnection and the coordinator; sofar-modbus is the HA-free device
library that does the actual register work.

Note on the ``sofar_modbus`` imports below: that's the third-party
``sofar-modbus`` PyPI library, not a self-import — this integration's own
package is ``custom_components.sofar_modbus``, a distinct namespace despite
the identical name. Confusing to read, but harmless: Home Assistant never
puts ``custom_components/`` on ``sys.path`` as a top-level package. Renaming
this integration's domain to avoid the collision was considered and
rejected — it would break every existing install's entity_ids and Energy
Dashboard history, which this project already goes out of its way to
protect (see the solax_modbus migration notes in README and the 0.3.14
restore-across-reboots work).
"""

from __future__ import annotations

import logging

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from sofar_modbus.modern.device import SofarInverter, identify

from .connection import build_connection, unit_id
from .const import CONF_READ_EPS, DEFAULT_SCAN_INTERVAL
from .coordinator import SofarConfigEntry, SofarDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SELECT, Platform.NUMBER, Platform.SWITCH, Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: SofarConfigEntry) -> bool:
    connection = build_connection(entry.data)
    entry.async_on_unload(connection.close)

    unit = connection.for_unit(unit_id(entry.data))
    serial = entry.unique_id
    inverter_type, model = identify(serial) if serial else (None, None)
    device = SofarInverter(
        unit,
        serial_number=serial if inverter_type else None,
        model=model if inverter_type else None,
        inverter_type=inverter_type,
        read_eps=entry.data.get(CONF_READ_EPS, False),
    )

    coordinator = SofarDataUpdateCoordinator(hass, entry, connection, device, DEFAULT_SCAN_INTERVAL)

    # Platforms build their entity lists from served_components, so setup must block here even when pre-identified (see CHANGELOG's 0.1.10 entry).
    await coordinator.async_config_entry_first_refresh()
    if not device.inverter_type:
        raise ConfigEntryNotReady(f"Unrecognized Sofar inverter model for {entry.title}")

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SofarConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
