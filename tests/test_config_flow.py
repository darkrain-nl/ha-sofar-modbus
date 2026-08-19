"""Tests for the Sofar Modbus config flow."""

from __future__ import annotations

from collections.abc import Callable
from unittest.mock import patch

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from modbus_connection import IllegalDataAddressError, ModbusConnectionError
from modbus_connection.mock import MockModbusConnection, MockModbusUnit
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sofar_modbus.config_flow import SofarUnrecognizedError, _async_probe
from custom_components.sofar_modbus.const import CONF_MODBUS_ADDR, CONF_READ_EPS, DOMAIN

MOCK_FORM_INPUT = {
    CONF_NAME: "Sofar Inverter",
    CONF_HOST: "192.168.1.100",
    CONF_PORT: 502,
    CONF_MODBUS_ADDR: 1,
}

MOCK_USER_INPUT = {**MOCK_FORM_INPUT, CONF_READ_EPS: False}

MOCK_HYBRID_SERIAL = "SP1XXES100XX"
MOCK_HYBRID_MODEL = "HYDxxKTL-3P"


def _seed_serial(unit: MockModbusUnit, serial: str) -> None:
    padded = serial.ljust(14, "\x00")
    for i in range(7):
        hi, lo = ord(padded[2 * i]), ord(padded[2 * i + 1])
        unit.holding[0x445 + i] = (hi << 8) | lo


def _seed_hybrid_inverter(unit: MockModbusUnit, serial: str = MOCK_HYBRID_SERIAL) -> None:
    """Seed registers for a hybrid inverter with EPS support."""
    _seed_serial(unit, serial)
    unit.holding[0x0404] = 2  # Running
    unit.holding[0x0484] = 5000  # 50.00 Hz
    unit.holding[0x1029] = 2  # EPS: TURN_ON_ENABLE_COLD_START
    unit.holding[0x102A] = 0  # EPS wait time


async def test_form_user_step(hass: HomeAssistant) -> None:
    """Test the initial user form is displayed with defaults."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_USER},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {}


async def test_user_step_success(hass: HomeAssistant) -> None:
    """Test successful user flow creating a config entry."""
    with (
        patch(
            "custom_components.sofar_modbus.config_flow._async_probe",
            return_value=("SS2ES104N5S445", "4.4KTLX-G3", False),
        ),
        patch(
            "custom_components.sofar_modbus.async_setup_entry",
            return_value=True,
        ) as mock_setup,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data=MOCK_FORM_INPUT,
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Sofar Inverter (4.4KTLX-G3)"
    assert result["data"] == MOCK_USER_INPUT
    assert result["result"].unique_id == "SS2ES104N5S445"
    assert len(mock_setup.mock_calls) == 1


async def test_user_step_success_without_model(hass: HomeAssistant) -> None:
    """Test successful flow when model is None."""
    with (
        patch(
            "custom_components.sofar_modbus.config_flow._async_probe",
            return_value=("SS2ES104N5S445", None, False),
        ),
        patch("custom_components.sofar_modbus.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data=MOCK_FORM_INPUT,
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Sofar Inverter"
    assert result["data"] == MOCK_USER_INPUT


async def test_user_step_cannot_connect(hass: HomeAssistant) -> None:
    """Test cannot_connect error when Modbus connection fails."""
    with patch(
        "custom_components.sofar_modbus.config_flow._async_probe",
        side_effect=ModbusConnectionError("Connection timed out"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data=MOCK_FORM_INPUT,
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "cannot_connect"}

    # Recover with successful submission
    with (
        patch(
            "custom_components.sofar_modbus.config_flow._async_probe",
            return_value=("SS2ES104N5S445", "4.4KTLX-G3", False),
        ),
        patch("custom_components.sofar_modbus.async_setup_entry", return_value=True),
    ):
        result2 = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            MOCK_FORM_INPUT,
        )

    assert result2["type"] is FlowResultType.CREATE_ENTRY


async def test_user_step_unrecognized_inverter(hass: HomeAssistant) -> None:
    """Test unrecognized_inverter error when serial matches no known model."""
    with patch(
        "custom_components.sofar_modbus.config_flow._async_probe",
        side_effect=SofarUnrecognizedError("UNKNOWN_SERIAL"),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data=MOCK_FORM_INPUT,
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": "unrecognized_inverter"}


async def test_user_step_already_configured(hass: HomeAssistant) -> None:
    """Test aborting when inverter is already configured."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="SS2ES104N5S445",
        data=MOCK_USER_INPUT,
    )
    entry.add_to_hass(hass)

    with patch(
        "custom_components.sofar_modbus.config_flow._async_probe",
        return_value=("SS2ES104N5S445", "4.4KTLX-G3", False),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data=MOCK_FORM_INPUT,
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize(
    ("seed_eps", "expected_read_eps"),
    [
        pytest.param(lambda unit: None, True, id="eps_supported"),
        pytest.param(
            lambda unit: unit.fail_read(0x1029, IllegalDataAddressError(), register_type="holding"),
            False,
            id="eps_unsupported",
        ),
    ],
)
async def test_user_step_probes_eps(
    hass: HomeAssistant,
    seed_eps: Callable[[MockModbusUnit], None],
    expected_read_eps: bool,
) -> None:
    """Test the EPS option is detected by probing, not asked of the user."""
    mock_conn = MockModbusConnection()
    unit = mock_conn.for_unit(1)
    _seed_hybrid_inverter(unit)
    seed_eps(unit)

    with (
        patch("custom_components.sofar_modbus.config_flow.build_connection", return_value=mock_conn),
        patch("custom_components.sofar_modbus.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USER},
            data=MOCK_FORM_INPUT,
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == f"Sofar Inverter ({MOCK_HYBRID_MODEL})"
    assert result["data"][CONF_READ_EPS] is expected_read_eps
    assert result["result"].unique_id == MOCK_HYBRID_SERIAL


async def test_async_probe_real_mock_connection() -> None:
    """Test the _async_probe function directly with MockModbusConnection."""
    mock_conn = MockModbusConnection()
    unit = mock_conn.for_unit(1)
    _seed_serial(unit, "SS2ES104N5S445")

    with patch("custom_components.sofar_modbus.config_flow.build_connection", return_value=mock_conn):
        serial, model, read_eps = await _async_probe(MOCK_FORM_INPUT)

    assert serial == "SS2ES104N5S445"
    assert model == "4.4 KTLX-G3"
    assert read_eps is False


async def test_async_probe_unrecognized_serial() -> None:
    """Test _async_probe raises SofarUnrecognizedError on unseeded/unknown serial."""
    mock_conn = MockModbusConnection()
    mock_conn.for_unit(1)  # unseeded registers -> zeroes

    with patch("custom_components.sofar_modbus.config_flow.build_connection", return_value=mock_conn):
        with pytest.raises(SofarUnrecognizedError):
            await _async_probe(MOCK_FORM_INPUT)


async def test_reconfigure_flow_form_and_success(hass: HomeAssistant) -> None:
    """Test reconfigure flow displays form and successfully updates config entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="SS2ES104N5S445",
        data=MOCK_USER_INPUT,
        title="Sofar Inverter (4.4 KTLX-G3)",
    )
    entry.add_to_hass(hass)

    # 1. Initialize reconfigure flow
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {}

    # 2. Submit new IP and port
    new_input = {
        CONF_HOST: "192.168.1.222",
        CONF_PORT: 5020,
        CONF_MODBUS_ADDR: 2,
    }
    with (
        patch(
            "custom_components.sofar_modbus.config_flow._async_probe",
            return_value=("SS2ES104N5S445", "4.4 KTLX-G3", True),
        ),
        patch(
            "custom_components.sofar_modbus.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input=new_input,
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_HOST] == "192.168.1.222"
    assert entry.data[CONF_PORT] == 5020
    assert entry.data[CONF_MODBUS_ADDR] == 2
    assert entry.data[CONF_READ_EPS] is True
    # Name preserved from previous entry data
    assert entry.data[CONF_NAME] == "Sofar Inverter"


async def test_reconfigure_flow_cannot_connect(hass: HomeAssistant) -> None:
    """Test reconfigure flow error handling when modbus connection fails."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="SS2ES104N5S445",
        data=MOCK_USER_INPUT,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )

    with patch(
        "custom_components.sofar_modbus.config_flow._async_probe",
        side_effect=ModbusConnectionError("Connection timed out"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_HOST: "10.0.0.1"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_reconfigure_flow_unrecognized_inverter(hass: HomeAssistant) -> None:
    """Test reconfigure flow error handling when inverter serial is unrecognized."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="SS2ES104N5S445",
        data=MOCK_USER_INPUT,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )

    with patch(
        "custom_components.sofar_modbus.config_flow._async_probe",
        side_effect=SofarUnrecognizedError("UNKNOWN"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_HOST: "192.168.1.100"},
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "unrecognized_inverter"}


async def test_reconfigure_flow_different_serial_aborts(hass: HomeAssistant) -> None:
    """Test reconfigure flow aborts when the new IP connects to a different inverter."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="SS2ES104N5S445",
        data=MOCK_USER_INPUT,
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={
            "source": config_entries.SOURCE_RECONFIGURE,
            "entry_id": entry.entry_id,
        },
    )

    with patch(
        "custom_components.sofar_modbus.config_flow._async_probe",
        return_value=("DIFFERENT_SERIAL_123", "HYD-6000-EP", False),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_HOST: "192.168.1.199"},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "different_serial"
