"""Tests for Sofar Modbus integration setup and unload."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant
from modbus_connection import ModbusConnectionError
from modbus_connection.mock import MockModbusConnection, MockModbusUnit
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sofar_modbus.const import CONF_MODBUS_ADDR, CONF_READ_EPS, DOMAIN
from custom_components.sofar_modbus.coordinator import SofarDataUpdateCoordinator

MOCK_CONFIG = {
    CONF_NAME: "Sofar Inverter",
    CONF_HOST: "192.168.1.100",
    CONF_PORT: 502,
    CONF_MODBUS_ADDR: 1,
    CONF_READ_EPS: False,
}


def _seed_pv_inverter(unit: MockModbusUnit, serial: str = "SS2ES104N5S445") -> None:
    """Seed holding registers for a PV inverter."""
    padded = serial.ljust(14, "\x00")
    for i in range(7):
        hi, lo = ord(padded[2 * i]), ord(padded[2 * i + 1])
        unit.holding[0x445 + i] = (hi << 8) | lo
    # Seed fast tier
    unit.holding[0x0404] = 2  # Running
    unit.holding[0x0484] = 5000  # 50.00 Hz
    # Seed slow tier
    unit.holding[0x0684] = 100
    unit.holding[0x1104] = 1


async def test_setup_and_unload_entry(hass: HomeAssistant) -> None:
    """Test standard setup and unload of a config entry."""
    mock_conn = MockModbusConnection()
    unit = mock_conn.for_unit(1)
    _seed_pv_inverter(unit)

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="SS2ES104N5S445",
        data=MOCK_CONFIG,
        title="Sofar Inverter (4.4 KTLX-G3)",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.sofar_modbus.build_connection", return_value=mock_conn):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, SofarDataUpdateCoordinator)

    # Unload
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED  # type: ignore[comparison-overlap]


async def test_setup_entry_not_ready_on_unrecognized(hass: HomeAssistant) -> None:
    """Test ConfigEntryNotReady when inverter identity is unrecognized."""
    mock_conn = MockModbusConnection()
    mock_conn.for_unit(1)  # unseeded -> 0s -> no matching type

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="UNKNOWN_SERIAL",
        data=MOCK_CONFIG,
        title="Sofar Inverter",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.sofar_modbus.build_connection", return_value=mock_conn):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_entry_not_ready_on_connection_error(hass: HomeAssistant) -> None:
    """Test ConfigEntryNotReady when unique_id is missing and initial poll fails."""
    mock_conn = MockModbusConnection()
    unit = mock_conn.for_unit(1)
    _seed_pv_inverter(unit)
    unit.fail_read(0x0445, ModbusConnectionError("Connection lost"))

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=None,
        data=MOCK_CONFIG,
        title="Sofar Inverter",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.sofar_modbus.build_connection", return_value=mock_conn):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_entry_retries_when_pre_identified_but_offline(hass: HomeAssistant) -> None:
    """A known-identity inverter offline at boot now retries setup rather than loading unavailable (sofar-modbus 0.1.10 dropped the sync prime() path — see CHANGELOG)."""
    mock_conn = MockModbusConnection()
    unit = mock_conn.for_unit(1)
    _seed_pv_inverter(unit)
    # Simulate inverter sleeping / offline in the dark
    unit.fail_read(0x0404, ModbusConnectionError("Inverter asleep in the dark"))

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="SS2ES104N5S445",
        data=MOCK_CONFIG,
        title="Sofar Inverter",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.sofar_modbus.build_connection", return_value=mock_conn):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_setup_entry_without_pre_identified_serial(hass: HomeAssistant) -> None:
    """Test setup when unique_id is not pre-identified on the entry."""
    mock_conn = MockModbusConnection()
    unit = mock_conn.for_unit(1)
    _seed_pv_inverter(unit, "SS2ES104N5S445")

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=None,
        data=MOCK_CONFIG,
        title="Sofar Inverter",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.sofar_modbus.build_connection", return_value=mock_conn):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.runtime_data.device.serial_number == "SS2ES104N5S445"


async def test_setup_entry_not_ready_on_unrecognized_serial_on_device(hass: HomeAssistant) -> None:
    """Test ConfigEntryNotReady when device serial answered but doesn't match a known type (line 52)."""
    mock_conn = MockModbusConnection()
    unit = mock_conn.for_unit(1)
    # Seed serial that is not in Sofar model database
    _seed_pv_inverter(unit, "UNKNOWN_SER_99")

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=None,
        data=MOCK_CONFIG,
        title="Sofar Inverter",
    )
    entry.add_to_hass(hass)

    with patch("custom_components.sofar_modbus.build_connection", return_value=mock_conn):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)

    assert entry.state is ConfigEntryState.SETUP_RETRY


def test_build_connection() -> None:
    """Test build_connection creates a ModbusConnection with TCP params."""
    from custom_components.sofar_modbus.connection import build_connection

    conn = build_connection(MOCK_CONFIG)
    assert conn is not None
