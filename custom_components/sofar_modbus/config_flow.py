"""Config flow — TCP only for Phase 1. Probes the device to get its serial
number for the unique_id, per the modbus-connection integration guide.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from modbus_connection import ModbusError

from sofar_modbus.modern.device import SofarInverter, identify  # the PyPI library, not a self-import — see __init__.py

from .connection import build_connection, unit_id
from .const import CONF_MODBUS_ADDR, CONF_READ_EPS, DEFAULT_MODBUS_ADDR, DEFAULT_NAME, DEFAULT_PORT, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
        vol.Optional(CONF_MODBUS_ADDR, default=DEFAULT_MODBUS_ADDR): int,
    }
)


class SofarUnrecognizedError(Exception):
    """The device answered, but its serial number matched no known Sofar model."""

    def __init__(self, serial: str) -> None:
        super().__init__(f"unrecognized Sofar inverter, serial number: {serial!r}")
        self.serial = serial


async def _async_probe(data: dict[str, Any]) -> tuple[str, str | None, bool]:
    """(serial, model, read_eps) or raises ModbusError/Unrecognized."""
    connection = build_connection(data)
    try:
        device = SofarInverter(connection.for_unit(unit_id(data)), read_eps=True)
        report = await device.async_update()
        if TYPE_CHECKING:
            assert device.serial_number is not None
        # inverter_type always carries the EPS bit from read_eps=True,
        # so check identify() directly instead of inverter_type.
        if not identify(device.serial_number)[0]:
            raise SofarUnrecognizedError(device.serial_number or "")
    finally:
        await connection.close()
    return device.serial_number or "", device.model, "eps" in report.updated


class SofarConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a Sofar Modbus config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                serial, model, read_eps = await _async_probe(user_input)
            except ModbusError:
                errors["base"] = "cannot_connect"
            except SofarUnrecognizedError:
                errors["base"] = "unrecognized_inverter"
            else:
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_configured()
                title = f"{user_input[CONF_NAME]} ({model})" if model else user_input[CONF_NAME]
                return self.async_create_entry(title=title, data={**user_input, CONF_READ_EPS: read_eps})

        return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle reconfiguration of the inverter connection."""
        errors: dict[str, str] = {}
        entry = self._get_reconfigure_entry()

        if user_input is not None:
            reconfig_data = {**entry.data, **user_input}
            try:
                serial, _, read_eps = await _async_probe(reconfig_data)
            except ModbusError:
                errors["base"] = "cannot_connect"
            except SofarUnrecognizedError:
                errors["base"] = "unrecognized_inverter"
            else:
                await self.async_set_unique_id(serial)
                self._abort_if_unique_id_mismatch(reason="different_serial")
                return self.async_update_reload_and_abort(entry, data={**reconfig_data, CONF_READ_EPS: read_eps})

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=entry.data.get(CONF_HOST)): str,
                vol.Required(CONF_PORT, default=entry.data.get(CONF_PORT, DEFAULT_PORT)): int,
                vol.Optional(CONF_MODBUS_ADDR, default=entry.data.get(CONF_MODBUS_ADDR, DEFAULT_MODBUS_ADDR)): int,
            }
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)
