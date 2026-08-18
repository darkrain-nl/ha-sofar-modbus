"""Sensor platform — one SensorEntity per row in SENSOR_DESCRIPTIONS below.

Only rows this inverter type actually serves get an entity. sofar_modbus has no
public "what will this device poll" surface (it settles that privately in
async_setup()) — only what one poll actually attempted, via UpdateReport. Since
every attempted component lands in exactly one of `updated` or `failed`, their
union is the served set, and the coordinator's first refresh (already run by
the time this platform is set up — see __init__.py) gives us one for free.

Each entity is also available independently of the others: sofar_modbus reads
components one at a time and contains a failed one in the poll's UpdateReport
rather than failing the whole update, so only the entities on a component that
actually failed this poll go unavailable — not all of them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import IntEnum
from typing import cast

from homeassistant.components.sensor import (
    RestoreSensor,
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfApparentPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfReactivePower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import SofarConfigEntry, SofarDataUpdateCoordinator
from .entity import SofarEntity, build_device_info

# GENERATOR: hand-written below — preserve verbatim when resyncing the generated block further down.


def _enum_label(member_name: str) -> str:
    """The label format used here and the `options` list declared on an
    ENUM sensor's description have to agree, or HA logs a state that isn't
    one of the declared options.
    """
    return " ".join(word.capitalize() for word in member_name.split("_"))


# How far below a total_increasing sensor's high-water mark a reading is still
# treated as read noise rather than a genuine reset. The two dips actually
# observed on this hardware (both on this integration and on solax_modbus
# reading the same inverter) were ~0.003% and ~0.0006% — a device-side torn
# read of a 32-bit counter split across two registers, not a comms failure.
# 1% leaves a wide margin above that noise while staying far below HA core's
# own reset_detected() threshold (drops below 90% of the previous value are
# treated as a meter reset — see homeassistant/components/sensor/recorder.py),
# so a genuine reset (e.g. a daily counter's midnight rollover to 0) still
# passes straight through untouched either way.
_TOTAL_INCREASING_DIP_TOLERANCE = 0.01


async def async_setup_entry(hass: HomeAssistant, entry: SofarConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    served = coordinator.served_components

    entities: list[SensorEntity] = [
        (
            SofarTotalSensor
            if description.state_class in (SensorStateClass.TOTAL, SensorStateClass.TOTAL_INCREASING)
            else SofarSensor
        )(coordinator, description)
        for description in SENSOR_DESCRIPTIONS
        if description.component in served  # not served by this inverter type otherwise
    ]
    entities.append(SofarCommunicationHealthSensor(coordinator))
    entities.append(SofarCommunicationHealthSuccessRateSensor(coordinator))
    entities.append(SofarCommunicationHealthLastErrorSensor(coordinator))
    entities.append(SofarCommunicationHealthLastErrorTimeSensor(coordinator))
    async_add_entities(entities)


class _SofarCommunicationHealthEntity(CoordinatorEntity[SofarDataUpdateCoordinator], SensorEntity):
    """Base for the communication_health family: coordinator bookkeeping, not
    a component on ``coordinator.device``, so there's no single component to
    gate ``available`` on the way ``SofarEntity.available`` does — and unlike
    ``SofarEntity``, these stay available even when the whole link is down
    (``coordinator.last_update_success`` is False), since that's exactly the
    moment a link-quality/last-error readout is most needed. success_rate/
    last_error/last_error_time are recorded on the coordinator even when a
    poll fails outright (see coordinator.py's ``_record_poll_outcome`` calls
    ahead of every ``raise UpdateFailed``), so there's always something
    correct to read here regardless of link state.
    """

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SofarDataUpdateCoordinator, unique_id_suffix: str) -> None:
        super().__init__(coordinator)
        serial = coordinator.device.serial_number
        self._attr_unique_id = f"{serial}_{unique_id_suffix}"
        self._attr_device_info = build_device_info(coordinator)

    @property
    def available(self) -> bool:
        return True


class SofarCommunicationHealthSensor(_SofarCommunicationHealthEntity):
    """Whole-device link-quality summary: one bad cycle in the last 20 dents this, not any one entity."""

    _attr_translation_key = "communication_health"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["good", "degraded", "poor", "unknown"]

    def __init__(self, coordinator: SofarDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "communication_health")

    @property
    def native_value(self) -> str:
        rate = self.coordinator.success_rate
        if rate is None:
            return "unknown"
        if rate == 100:
            return "good"
        if rate >= 80:
            return "degraded"
        return "poor"


class SofarCommunicationHealthSuccessRateSensor(_SofarCommunicationHealthEntity):
    """Same rolling window as `SofarCommunicationHealthSensor`, as a number instead of a bucket."""

    _attr_translation_key = "communication_health_success_rate"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: SofarDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "communication_health_success_rate")

    @property
    def native_value(self) -> float | None:
        return self.coordinator.success_rate


class SofarCommunicationHealthLastErrorSensor(_SofarCommunicationHealthEntity):
    """Type + message of the most recent poll error, if any. Not cleared by a later success."""

    _attr_translation_key = "communication_health_last_error"
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: SofarDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "communication_health_last_error")

    @property
    def native_value(self) -> str | None:
        return self.coordinator.last_error


class SofarCommunicationHealthLastErrorTimeSensor(_SofarCommunicationHealthEntity):
    """When the most recent poll error (see `SofarCommunicationHealthLastErrorSensor`) happened."""

    _attr_translation_key = "communication_health_last_error_time"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: SofarDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "communication_health_last_error_time")

    @property
    def native_value(self) -> datetime | None:
        return self.coordinator.last_error_time


class SofarSensor(SofarEntity, SensorEntity):
    """A read-only value off one of the device's Components."""

    entity_description: SofarSensorDescription

    def __init__(self, coordinator: SofarDataUpdateCoordinator, description: SofarSensorDescription) -> None:
        super().__init__(coordinator, description.key, description.component)
        self.entity_description = description

    @property
    def native_value(self) -> str | int | float | date | None:
        component = getattr(self.coordinator.device, self.entity_description.component)
        value = getattr(component, self.entity_description.key)
        # IntEnum.__str__ prints just the int since Python 3.11 (unlike plain
        # Enum) — translate to the label an ENUM-device-class sensor declared
        # in its `options`, rather than showing a bare number.
        if isinstance(value, IntEnum):
            return _enum_label(value.name)
        if isinstance(value, (int, float)) and self.entity_description.scale != 1.0:
            return value * self.entity_description.scale
        return cast("str | int | float | date | None", value)


class SofarTotalSensor(SofarEntity, RestoreSensor):
    """A long-term statistic: it holds its last value, and may outlive the device.

    Restored on startup so energy dashboard sensors never show `unknown`
    during the boot-up window or overnight, and so the dip guard's high-water
    mark anchors immediately rather than accepting a torn read on first poll.
    """

    entity_description: SofarSensorDescription

    def __init__(self, coordinator: SofarDataUpdateCoordinator, description: SofarSensorDescription) -> None:
        super().__init__(coordinator, description.key, description.component)
        self.entity_description = description
        self._total_increasing_high_water: float | None = None

    @property
    def available(self) -> bool:
        # Total and total_increasing counters hold available unconditionally
        # (even across link drops or offline nights) so long-term statistics
        # and the energy dashboard stay unbroken.
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (last_data := await self.async_get_last_sensor_data()) is not None:
            if last_data.native_value is not None:
                try:
                    val = float(str(last_data.native_value))
                    self._attr_native_value = val
                    self._total_increasing_high_water = val
                except (ValueError, TypeError):
                    pass

    @property
    def native_value(self) -> int | float | None:
        component = getattr(self.coordinator.device, self.entity_description.component)
        value = getattr(component, self.entity_description.key)
        if value is not None:
            if self.entity_description.state_class is SensorStateClass.TOTAL_INCREASING and isinstance(value, (int, float)):
                self._attr_native_value = self._smoothed_total_increasing(float(value))
            elif isinstance(value, (int, float)):
                self._attr_native_value = value
        if isinstance(self._attr_native_value, (int, float)):
            return self._attr_native_value
        return None

    def _smoothed_total_increasing(self, value: float) -> float:
        """Hold a total_increasing sensor at its high-water mark through a
        small dip (device-side torn read of a multi-register counter) instead
        of publishing a value HA would log as "not strictly increasing" — but
        let a genuine drop (a daily counter's midnight reset, an actual meter
        reset) through immediately. See _TOTAL_INCREASING_DIP_TOLERANCE.
        """
        high_water = self._total_increasing_high_water
        if high_water is None or value >= high_water or value < high_water * (1 - _TOTAL_INCREASING_DIP_TOLERANCE):
            self._total_increasing_high_water = value
            return value
        return high_water


# GENERATOR: generated below from plugin_sofar.py @ 27875b3b — do not
# hand-edit past this line. Resync by asking an agent to diff this block
# against the current upstream plugin_sofar.py, not by hand.


@dataclass(frozen=True, kw_only=True)
class SofarSensorDescription(SensorEntityDescription):
    """A real SensorEntityDescription, plus which Component the value comes from.

    Must subclass SensorEntityDescription (not just duck-type its fields) —
    SensorEntity reads attributes like entity_registry_visible_default and
    suggested_unit_of_measurement straight off entity_description with no
    _attr_ fallback, so a bespoke dataclass raises AttributeError on those.
    """

    component: str  # attribute name on SofarInverter, e.g. 'grid', 'pv_1_2', 'energy'
    scale: float = 1.0  # multiplies the raw value; for units HA has no kilo-variant of (e.g. VA)


SENSOR_DESCRIPTIONS: tuple[SofarSensorDescription, ...] = (
    SofarSensorDescription(
        key="system_state",
        component="state",
        translation_key="system_state",
        device_class=SensorDeviceClass.ENUM,
        options=[
            "Waiting",
            "Checking",
            "Grid Connected",
            "Emergency Power Supply",
            "Recoverable Fault",
            "Permanent Fault",
            "Upgrading",
            "Self Charging",
        ],
    ),
    SofarSensorDescription(
        key="fault_1",
        component="state",
        translation_key="fault_1",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SofarSensorDescription(
        key="fault_2",
        component="state",
        translation_key="fault_2",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SofarSensorDescription(
        key="fault_3",
        component="state",
        translation_key="fault_3",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SofarSensorDescription(
        key="fault_4",
        component="state",
        translation_key="fault_4",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SofarSensorDescription(
        key="fault_5",
        component="state",
        translation_key="fault_5",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SofarSensorDescription(
        key="fault_6",
        component="state",
        translation_key="fault_6",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SofarSensorDescription(
        key="fault_7",
        component="state",
        translation_key="fault_7",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SofarSensorDescription(
        key="fault_8",
        component="state",
        translation_key="fault_8",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SofarSensorDescription(
        key="fault_9",
        component="state",
        translation_key="fault_9",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SofarSensorDescription(
        key="fault_10",
        component="state",
        translation_key="fault_10",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SofarSensorDescription(
        key="fault_11",
        component="state",
        translation_key="fault_11",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SofarSensorDescription(
        key="fault_12",
        component="state",
        translation_key="fault_12",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SofarSensorDescription(
        key="waiting_time",
        component="state",
        translation_key="waiting_time",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SofarSensorDescription(
        key="inverter_temperature_1",
        component="state",
        translation_key="inverter_temperature_1",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SofarSensorDescription(
        key="inverter_temperature_2",
        component="state",
        translation_key="inverter_temperature_2",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SofarSensorDescription(
        key="heatsink_temperature_1",
        component="state",
        translation_key="heatsink_temperature_1",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SofarSensorDescription(
        key="heatsink_temperature_2",
        component="state",
        translation_key="heatsink_temperature_2",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SofarSensorDescription(
        key="module_temperature_1",
        component="state",
        translation_key="module_temperature_1",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SofarSensorDescription(
        key="module_temperature_2",
        component="state",
        translation_key="module_temperature_2",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SofarSensorDescription(
        key="rtc",
        component="identity",
        translation_key="rtc",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:clock",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="serial_number",
        component="identity",
        translation_key="serial_number",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SofarSensorDescription(
        key="hardware_version",
        component="identity",
        translation_key="hardware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="software_version",
        component="identity",
        translation_key="software_version",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SofarSensorDescription(
        key="grid_frequency",
        component="grid",
        translation_key="grid_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="active_power_output_total",
        component="grid",
        translation_key="active_power_output_total",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="reactive_power_output_total",
        component="grid",
        translation_key="reactive_power_output_total",
        device_class=SensorDeviceClass.REACTIVE_POWER,
        # kvar, not var — see sofar-modbus 0.1.11.
        native_unit_of_measurement=UnitOfReactivePower.KILO_VOLT_AMPERE_REACTIVE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="apparent_power_output_total",
        component="grid",
        translation_key="apparent_power_output_total",
        device_class=SensorDeviceClass.APPARENT_POWER,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        # No kVA in HA's UnitOfApparentPower — scale the kVA-scaled register to VA instead. See sofar-modbus 0.1.11.
        scale=1000,
    ),
    SofarSensorDescription(
        key="active_power_pcc_total",
        component="grid",
        translation_key="active_power_pcc_total",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="reactive_power_pcc_total",
        component="grid",
        translation_key="reactive_power_pcc_total",
        device_class=SensorDeviceClass.REACTIVE_POWER,
        # kvar, not var — see sofar-modbus 0.1.11.
        native_unit_of_measurement=UnitOfReactivePower.KILO_VOLT_AMPERE_REACTIVE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="apparent_power_pcc_total",
        component="grid",
        translation_key="apparent_power_pcc_total",
        device_class=SensorDeviceClass.APPARENT_POWER,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        # No kVA in HA's UnitOfApparentPower — scale the kVA-scaled register to VA instead. See sofar-modbus 0.1.11.
        scale=1000,
    ),
    SofarSensorDescription(
        key="voltage_l1",
        component="grid",
        translation_key="voltage_l1",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    SofarSensorDescription(
        key="current_output_l1",
        component="grid",
        translation_key="current_output_l1",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="active_power_output_l1",
        component="grid",
        translation_key="active_power_output_l1",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="reactive_power_output_l1",
        component="grid",
        translation_key="reactive_power_output_l1",
        device_class=SensorDeviceClass.REACTIVE_POWER,
        # kvar, not var — see sofar-modbus 0.1.11.
        native_unit_of_measurement=UnitOfReactivePower.KILO_VOLT_AMPERE_REACTIVE,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="power_factor_output_l1",
        component="grid",
        translation_key="power_factor_output_l1",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="current_pcc_l1",
        component="grid",
        translation_key="current_pcc_l1",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="active_power_pcc_l1",
        component="grid",
        translation_key="active_power_pcc_l1",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="reactive_power_pcc_l1",
        component="grid",
        translation_key="reactive_power_pcc_l1",
        device_class=SensorDeviceClass.REACTIVE_POWER,
        # kvar, not var — see sofar-modbus 0.1.11.
        native_unit_of_measurement=UnitOfReactivePower.KILO_VOLT_AMPERE_REACTIVE,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="power_factor_pcc_l1",
        component="grid",
        translation_key="power_factor_pcc_l1",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="voltage_l2",
        component="grid",
        translation_key="voltage_l2",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    SofarSensorDescription(
        key="current_output_l2",
        component="grid",
        translation_key="current_output_l2",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="active_power_output_l2",
        component="grid",
        translation_key="active_power_output_l2",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="reactive_power_output_l2",
        component="grid",
        translation_key="reactive_power_output_l2",
        device_class=SensorDeviceClass.REACTIVE_POWER,
        # kvar, not var — see sofar-modbus 0.1.11.
        native_unit_of_measurement=UnitOfReactivePower.KILO_VOLT_AMPERE_REACTIVE,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="power_factor_output_l2",
        component="grid",
        translation_key="power_factor_output_l2",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="current_pcc_l2",
        component="grid",
        translation_key="current_pcc_l2",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="active_power_pcc_l2",
        component="grid",
        translation_key="active_power_pcc_l2",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="reactive_power_pcc_l2",
        component="grid",
        translation_key="reactive_power_pcc_l2",
        device_class=SensorDeviceClass.REACTIVE_POWER,
        # kvar, not var — see sofar-modbus 0.1.11.
        native_unit_of_measurement=UnitOfReactivePower.KILO_VOLT_AMPERE_REACTIVE,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="power_factor_pcc_l2",
        component="grid",
        translation_key="power_factor_pcc_l2",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="voltage_l3",
        component="grid",
        translation_key="voltage_l3",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    SofarSensorDescription(
        key="current_output_l3",
        component="grid",
        translation_key="current_output_l3",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="active_power_output_l3",
        component="grid",
        translation_key="active_power_output_l3",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="reactive_power_output_l3",
        component="grid",
        translation_key="reactive_power_output_l3",
        device_class=SensorDeviceClass.REACTIVE_POWER,
        # kvar, not var — see sofar-modbus 0.1.11.
        native_unit_of_measurement=UnitOfReactivePower.KILO_VOLT_AMPERE_REACTIVE,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="power_factor_output_l3",
        component="grid",
        translation_key="power_factor_output_l3",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="current_pcc_l3",
        component="grid",
        translation_key="current_pcc_l3",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="active_power_pcc_l3",
        component="grid",
        translation_key="active_power_pcc_l3",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="reactive_power_pcc_l3",
        component="grid",
        translation_key="reactive_power_pcc_l3",
        device_class=SensorDeviceClass.REACTIVE_POWER,
        # kvar, not var — see sofar-modbus 0.1.11.
        native_unit_of_measurement=UnitOfReactivePower.KILO_VOLT_AMPERE_REACTIVE,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="power_factor_pcc_l3",
        component="grid",
        translation_key="power_factor_pcc_l3",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="active_power_pv_ext",
        component="grid",
        translation_key="active_power_pv_ext",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="active_power_load_sys",
        component="grid",
        translation_key="active_power_load_sys",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="voltage_phase_l1n",
        component="grid",
        translation_key="voltage_phase_l1n",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    SofarSensorDescription(
        key="current_output_l1n",
        component="grid",
        translation_key="current_output_l1n",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="active_power_output_l1n",
        component="grid",
        translation_key="active_power_output_l1n",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="current_pcc_l1n",
        component="grid",
        translation_key="current_pcc_l1n",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="active_power_pcc_l1n",
        component="grid",
        translation_key="active_power_pcc_l1n",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="voltage_phase_l2n",
        component="grid",
        translation_key="voltage_phase_l2n",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    SofarSensorDescription(
        key="current_output_l2n",
        component="grid",
        translation_key="current_output_l2n",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="active_power_output_l2n",
        component="grid",
        translation_key="active_power_output_l2n",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="current_pcc_l2n",
        component="grid",
        translation_key="current_pcc_l2n",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="active_power_pcc_l2n",
        component="grid",
        translation_key="active_power_pcc_l2n",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="voltage_line_l1",
        component="grid",
        translation_key="voltage_line_l1",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    SofarSensorDescription(
        key="voltage_line_l2",
        component="grid",
        translation_key="voltage_line_l2",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    SofarSensorDescription(
        key="voltage_line_l3",
        component="grid",
        translation_key="voltage_line_l3",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    SofarSensorDescription(
        key="active_power_offgrid_total",
        component="offgrid",
        translation_key="active_power_offgrid_total",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="reactive_power_offgrid_total",
        component="offgrid",
        translation_key="reactive_power_offgrid_total",
        device_class=SensorDeviceClass.REACTIVE_POWER,
        # kvar, not var — see sofar-modbus 0.1.11.
        native_unit_of_measurement=UnitOfReactivePower.KILO_VOLT_AMPERE_REACTIVE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="apparent_power_offgrid_total",
        component="offgrid",
        translation_key="apparent_power_offgrid_total",
        device_class=SensorDeviceClass.APPARENT_POWER,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        # No kVA in HA's UnitOfApparentPower — scale the kVA-scaled register to VA instead. See sofar-modbus 0.1.11.
        scale=1000,
    ),
    SofarSensorDescription(
        key="offgrid_frequency",
        component="offgrid",
        translation_key="offgrid_frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="offgrid_voltage",
        component="offgrid_single_phase",
        translation_key="offgrid_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SofarSensorDescription(
        key="offgrid_voltage_l1",
        component="offgrid_three_phase",
        translation_key="offgrid_voltage_l1",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SofarSensorDescription(
        key="offgrid_current_output",
        component="offgrid_single_phase",
        translation_key="offgrid_current_output",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="offgrid_current_output_l1",
        component="offgrid_three_phase",
        translation_key="offgrid_current_output_l1",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="offgrid_active_power_output",
        component="offgrid_single_phase",
        translation_key="offgrid_active_power_output",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="offgrid_active_power_output_l1",
        component="offgrid_three_phase",
        translation_key="offgrid_active_power_output_l1",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="offgrid_reactive_power_output",
        component="offgrid_single_phase",
        translation_key="offgrid_reactive_power_output",
        device_class=SensorDeviceClass.REACTIVE_POWER,
        # kvar, not var — see sofar-modbus 0.1.11.
        native_unit_of_measurement=UnitOfReactivePower.KILO_VOLT_AMPERE_REACTIVE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="offgrid_reactive_power_output_l1",
        component="offgrid_three_phase",
        translation_key="offgrid_reactive_power_output_l1",
        device_class=SensorDeviceClass.REACTIVE_POWER,
        # kvar, not var — see sofar-modbus 0.1.11.
        native_unit_of_measurement=UnitOfReactivePower.KILO_VOLT_AMPERE_REACTIVE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="offgrid_apparent_power_output",
        component="offgrid_single_phase",
        translation_key="offgrid_apparent_power_output",
        device_class=SensorDeviceClass.APPARENT_POWER,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        # No kVA in HA's UnitOfApparentPower — scale the kVA-scaled register to VA instead. See sofar-modbus 0.1.11.
        scale=1000,
    ),
    SofarSensorDescription(
        key="offgrid_apparent_power_output_l1",
        component="offgrid_three_phase",
        translation_key="offgrid_apparent_power_output_l1",
        device_class=SensorDeviceClass.APPARENT_POWER,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        # No kVA in HA's UnitOfApparentPower — scale the kVA-scaled register to VA instead. See sofar-modbus 0.1.11.
        scale=1000,
    ),
    SofarSensorDescription(
        key="offgrid_loadpeakratio",
        component="offgrid_single_phase",
        translation_key="offgrid_loadpeakratio",
        # Dimensionless per-unit ratio, not apparent power — see sofar-modbus 0.1.11.
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="offgrid_loadpeakratio_l1",
        component="offgrid_three_phase",
        translation_key="offgrid_loadpeakratio_l1",
        # Dimensionless per-unit ratio, not apparent power — see sofar-modbus 0.1.11.
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="offgrid_voltage_l2",
        component="offgrid_three_phase",
        translation_key="offgrid_voltage_l2",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SofarSensorDescription(
        key="offgrid_current_output_l2",
        component="offgrid_three_phase",
        translation_key="offgrid_current_output_l2",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="offgrid_active_power_output_l2",
        component="offgrid_three_phase",
        translation_key="offgrid_active_power_output_l2",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="offgrid_reactive_power_output_l2",
        component="offgrid_three_phase",
        translation_key="offgrid_reactive_power_output_l2",
        device_class=SensorDeviceClass.REACTIVE_POWER,
        # kvar, not var — see sofar-modbus 0.1.11.
        native_unit_of_measurement=UnitOfReactivePower.KILO_VOLT_AMPERE_REACTIVE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="offgrid_apparent_power_output_l2",
        component="offgrid_three_phase",
        translation_key="offgrid_apparent_power_output_l2",
        device_class=SensorDeviceClass.APPARENT_POWER,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        # No kVA in HA's UnitOfApparentPower — scale the kVA-scaled register to VA instead. See sofar-modbus 0.1.11.
        scale=1000,
    ),
    SofarSensorDescription(
        key="offgrid_loadpeakratio_l2",
        component="offgrid_three_phase",
        translation_key="offgrid_loadpeakratio_l2",
        # Dimensionless per-unit ratio, not apparent power — see sofar-modbus 0.1.11.
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="offgrid_voltage_l3",
        component="offgrid_three_phase",
        translation_key="offgrid_voltage_l3",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SofarSensorDescription(
        key="offgrid_current_output_l3",
        component="offgrid_three_phase",
        translation_key="offgrid_current_output_l3",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="offgrid_active_power_output_l3",
        component="offgrid_three_phase",
        translation_key="offgrid_active_power_output_l3",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="offgrid_reactive_power_output_l3",
        component="offgrid_three_phase",
        translation_key="offgrid_reactive_power_output_l3",
        device_class=SensorDeviceClass.REACTIVE_POWER,
        # kvar, not var — see sofar-modbus 0.1.11.
        native_unit_of_measurement=UnitOfReactivePower.KILO_VOLT_AMPERE_REACTIVE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="offgrid_apparent_power_output_l3",
        component="offgrid_three_phase",
        translation_key="offgrid_apparent_power_output_l3",
        device_class=SensorDeviceClass.APPARENT_POWER,
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        # No kVA in HA's UnitOfApparentPower — scale the kVA-scaled register to VA instead. See sofar-modbus 0.1.11.
        scale=1000,
    ),
    SofarSensorDescription(
        key="offgrid_loadpeakratio_l3",
        component="offgrid_three_phase",
        translation_key="offgrid_loadpeakratio_l3",
        # Dimensionless per-unit ratio, not apparent power — see sofar-modbus 0.1.11.
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="offgrid_voltage_output_l1n",
        component="offgrid_three_phase",
        translation_key="offgrid_voltage_output_l1n",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SofarSensorDescription(
        key="offgrid_current_output_l1n",
        component="offgrid_three_phase",
        translation_key="offgrid_current_output_l1n",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="offgrid_active_power_output_l1n",
        component="offgrid_three_phase",
        translation_key="offgrid_active_power_output_l1n",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="offgrid_voltage_output_l2n",
        component="offgrid_three_phase",
        translation_key="offgrid_voltage_output_l2n",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SofarSensorDescription(
        key="offgrid_current_output_l2n",
        component="offgrid_three_phase",
        translation_key="offgrid_current_output_l2n",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="offgrid_active_power_output_l2n",
        component="offgrid_three_phase",
        translation_key="offgrid_active_power_output_l2n",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="pv_voltage_1",
        component="pv_1_2",
        translation_key="pv_voltage_1",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    SofarSensorDescription(
        key="pv_current_1",
        component="pv_1_2",
        translation_key="pv_current_1",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="pv_power_1",
        component="pv_1_2",
        translation_key="pv_power_1",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="pv_voltage_2",
        component="pv_1_2",
        translation_key="pv_voltage_2",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    SofarSensorDescription(
        key="pv_current_2",
        component="pv_1_2",
        translation_key="pv_current_2",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="pv_power_2",
        component="pv_1_2",
        translation_key="pv_power_2",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="pv_voltage_3",
        component="pv_3",
        translation_key="pv_voltage_3",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    SofarSensorDescription(
        key="pv_current_3",
        component="pv_3",
        translation_key="pv_current_3",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="pv_power_3",
        component="pv_3",
        translation_key="pv_power_3",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="pv_voltage_4",
        component="pv_4",
        translation_key="pv_voltage_4",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    SofarSensorDescription(
        key="pv_current_4",
        component="pv_4",
        translation_key="pv_current_4",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="pv_power_4",
        component="pv_4",
        translation_key="pv_power_4",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="pv_voltage_5",
        component="pv_5_6",
        translation_key="pv_voltage_5",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    SofarSensorDescription(
        key="pv_current_5",
        component="pv_5_6",
        translation_key="pv_current_5",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="pv_power_5",
        component="pv_5_6",
        translation_key="pv_power_5",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="pv_voltage_6",
        component="pv_5_6",
        translation_key="pv_voltage_6",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    SofarSensorDescription(
        key="pv_current_6",
        component="pv_5_6",
        translation_key="pv_current_6",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="pv_power_6",
        component="pv_5_6",
        translation_key="pv_power_6",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="pv_voltage_7",
        component="pv_7_8",
        translation_key="pv_voltage_7",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    SofarSensorDescription(
        key="pv_current_7",
        component="pv_7_8",
        translation_key="pv_current_7",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="pv_power_7",
        component="pv_7_8",
        translation_key="pv_power_7",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="pv_voltage_8",
        component="pv_7_8",
        translation_key="pv_voltage_8",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    SofarSensorDescription(
        key="pv_current_8",
        component="pv_7_8",
        translation_key="pv_current_8",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="pv_power_8",
        component="pv_7_8",
        translation_key="pv_power_8",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="pv_voltage_9",
        component="pv_9_10",
        translation_key="pv_voltage_9",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    SofarSensorDescription(
        key="pv_current_9",
        component="pv_9_10",
        translation_key="pv_current_9",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="pv_power_9",
        component="pv_9_10",
        translation_key="pv_power_9",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="pv_voltage_10",
        component="pv_9_10",
        translation_key="pv_voltage_10",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    SofarSensorDescription(
        key="pv_current_10",
        component="pv_9_10",
        translation_key="pv_current_10",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="pv_power_10",
        component="pv_9_10",
        translation_key="pv_power_10",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="pv_power_total",
        component="pv_1_2",
        translation_key="pv_power_total",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SofarSensorDescription(
        key="battery_voltage_1",
        component="battery_1_2",
        translation_key="battery_voltage_1",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
    ),
    SofarSensorDescription(
        key="battery_current_1",
        component="battery_1_2",
        translation_key="battery_current_1",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="battery_power_1",
        component="battery_1_2",
        translation_key="battery_power_1",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="battery_temperature_1",
        component="battery_1_2",
        translation_key="battery_temperature_1",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SofarSensorDescription(
        key="battery_capacity_1",
        component="battery_1_2",
        translation_key="battery_capacity_1",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SofarSensorDescription(
        key="battery_state_of_health_1",
        component="battery_1_2",
        translation_key="battery_state_of_health_1",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-heart",
    ),
    SofarSensorDescription(
        key="battery_charge_cycle_1",
        component="battery_1_2",
        translation_key="battery_charge_cycle_1",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SofarSensorDescription(
        key="battery_voltage_2",
        component="battery_1_2",
        translation_key="battery_voltage_2",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_current_2",
        component="battery_1_2",
        translation_key="battery_current_2",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="battery_power_2",
        component="battery_1_2",
        translation_key="battery_power_2",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="battery_temperature_2",
        component="battery_1_2",
        translation_key="battery_temperature_2",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_capacity_2",
        component="battery_1_2",
        translation_key="battery_capacity_2",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_state_of_health_2",
        component="battery_1_2",
        translation_key="battery_state_of_health_2",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-heart",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_charge_cycle_2",
        component="battery_1_2",
        translation_key="battery_charge_cycle_2",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_voltage_3",
        component="battery_3_8",
        translation_key="battery_voltage_3",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_current_3",
        component="battery_3_8",
        translation_key="battery_current_3",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="battery_power_3",
        component="battery_3_8",
        translation_key="battery_power_3",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="battery_temperature_3",
        component="battery_3_8",
        translation_key="battery_temperature_3",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_capacity_3",
        component="battery_3_8",
        translation_key="battery_capacity_3",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_state_of_health_3",
        component="battery_3_8",
        translation_key="battery_state_of_health_3",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-heart",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_charge_cycle_3",
        component="battery_3_8",
        translation_key="battery_charge_cycle_3",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_voltage_4",
        component="battery_3_8",
        translation_key="battery_voltage_4",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_current_4",
        component="battery_3_8",
        translation_key="battery_current_4",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="battery_power_4",
        component="battery_3_8",
        translation_key="battery_power_4",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="battery_temperature_4",
        component="battery_3_8",
        translation_key="battery_temperature_4",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_capacity_4",
        component="battery_3_8",
        translation_key="battery_capacity_4",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_state_of_health_4",
        component="battery_3_8",
        translation_key="battery_state_of_health_4",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-heart",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_charge_cycle_4",
        component="battery_3_8",
        translation_key="battery_charge_cycle_4",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_voltage_5",
        component="battery_3_8",
        translation_key="battery_voltage_5",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_current_5",
        component="battery_3_8",
        translation_key="battery_current_5",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="battery_power_5",
        component="battery_3_8",
        translation_key="battery_power_5",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="battery_temperature_5",
        component="battery_3_8",
        translation_key="battery_temperature_5",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_capacity_5",
        component="battery_3_8",
        translation_key="battery_capacity_5",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_state_of_health_5",
        component="battery_3_8",
        translation_key="battery_state_of_health_5",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-heart",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_charge_cycle_5",
        component="battery_3_8",
        translation_key="battery_charge_cycle_5",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_voltage_6",
        component="battery_3_8",
        translation_key="battery_voltage_6",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_current_6",
        component="battery_3_8",
        translation_key="battery_current_6",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="battery_power_6",
        component="battery_3_8",
        translation_key="battery_power_6",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="battery_temperature_6",
        component="battery_3_8",
        translation_key="battery_temperature_6",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_capacity_6",
        component="battery_3_8",
        translation_key="battery_capacity_6",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_state_of_health_6",
        component="battery_3_8",
        translation_key="battery_state_of_health_6",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-heart",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_charge_cycle_6",
        component="battery_3_8",
        translation_key="battery_charge_cycle_6",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_voltage_7",
        component="battery_3_8",
        translation_key="battery_voltage_7",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_current_7",
        component="battery_3_8",
        translation_key="battery_current_7",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="battery_power_7",
        component="battery_3_8",
        translation_key="battery_power_7",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="battery_temperature_7",
        component="battery_3_8",
        translation_key="battery_temperature_7",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_capacity_7",
        component="battery_3_8",
        translation_key="battery_capacity_7",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_state_of_health_7",
        component="battery_3_8",
        translation_key="battery_state_of_health_7",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-heart",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_charge_cycle_7",
        component="battery_3_8",
        translation_key="battery_charge_cycle_7",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_voltage_8",
        component="battery_3_8",
        translation_key="battery_voltage_8",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_current_8",
        component="battery_3_8",
        translation_key="battery_current_8",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="battery_power_8",
        component="battery_3_8",
        translation_key="battery_power_8",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="battery_temperature_8",
        component="battery_3_8",
        translation_key="battery_temperature_8",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_capacity_8",
        component="battery_3_8",
        translation_key="battery_capacity_8",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_state_of_health_8",
        component="battery_3_8",
        translation_key="battery_state_of_health_8",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-heart",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_charge_cycle_8",
        component="battery_3_8",
        translation_key="battery_charge_cycle_8",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_power_total",
        component="battery_totals",
        translation_key="battery_power_total",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.KILO_WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SofarSensorDescription(
        key="battery_capacity_total",
        component="battery_totals",
        translation_key="battery_capacity_total",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SofarSensorDescription(
        key="battery_state_of_health_total",
        component="battery_totals",
        translation_key="battery_state_of_health_total",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-heart",
    ),
    SofarSensorDescription(
        key="solar_generation_today",
        component="energy",
        translation_key="solar_generation_today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="solar_generation_total",
        component="energy",
        translation_key="solar_generation_total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="load_consumption_today",
        component="energy",
        translation_key="load_consumption_today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="load_consumption_total",
        component="energy",
        translation_key="load_consumption_total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="import_energy_today",
        component="energy",
        translation_key="import_energy_today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:home-import-outline",
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="import_energy_total",
        component="energy",
        translation_key="import_energy_total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:home-import-outline",
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="export_energy_today",
        component="energy",
        translation_key="export_energy_today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:home-export-outline",
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="export_energy_total",
        component="energy",
        translation_key="export_energy_total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:home-export-outline",
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="battery_input_energy_today",
        component="battery_energy",
        translation_key="battery_input_energy_today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-arrow-up",
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="battery_input_energy_total",
        component="battery_energy",
        translation_key="battery_input_energy_total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-arrow-up",
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="battery_output_energy_today",
        component="battery_energy",
        translation_key="battery_output_energy_today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-arrow-down",
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="battery_output_energy_total",
        component="battery_energy",
        translation_key="battery_output_energy_total",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:battery-arrow-down",
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="passive_eps_wait_time",
        component="eps",
        translation_key="passive_eps_wait_time",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="battery_active_control",
        component="battery_active_control",
        translation_key="battery_active_control",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="parallel_control",
        component="parallel",
        translation_key="parallel_control",
    ),
    SofarSensorDescription(
        key="parallel_masterslave",
        component="parallel",
        translation_key="parallel_masterslave",
        device_class=SensorDeviceClass.ENUM,
        options=["Slave", "Master"],
    ),
    SofarSensorDescription(
        key="bat_config_id",
        component="battery_config_id",
        translation_key="bat_config_id",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-check-outline",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="bat_config_address_1",
        component="battery_config_id",
        translation_key="bat_config_address_1",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-check-outline",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="bat_config_protocol",
        component="battery_config_id",
        translation_key="bat_config_protocol",
        device_class=SensorDeviceClass.ENUM,
        options=[
            "First Flight Built In Bms Default",
            "Pie Energy Protocol Pylon",
            "First Flight Protocol General",
            "Amass",
            "Lg",
            "Alphaess",
            "Catl",
            "Weco",
            "Fronus",
            "Ems",
            "Nilar",
            "Bts 5k",
            "Move For",
        ],
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-check-outline",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="bat_config_overvoltage_protection",
        component="battery_config_id",
        translation_key="bat_config_overvoltage_protection",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-check-outline",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="bat_config_charging_voltage",
        component="battery_config",
        translation_key="bat_config_charging_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-check-outline",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="bat_config_undervoltage_protection",
        component="battery_config",
        translation_key="bat_config_undervoltage_protection",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-check-outline",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="bat_config_minimum_discharge_voltage",
        component="battery_config",
        translation_key="bat_config_minimum_discharge_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-check-outline",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="bat_config_maximum_charge_current_limit",
        component="battery_config",
        translation_key="bat_config_maximum_charge_current_limit",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-check-outline",
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="bat_config_maximum_discharge_current_limit",
        component="battery_config",
        translation_key="bat_config_maximum_discharge_current_limit",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-check-outline",
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
    ),
    SofarSensorDescription(
        key="bat_config_depth_of_discharge",
        component="battery_config",
        translation_key="bat_config_depth_of_discharge",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-check-outline",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="bat_config_end_of_discharge",
        component="battery_config",
        translation_key="bat_config_end_of_discharge",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-check-outline",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="bat_config_capacity",
        component="battery_config",
        translation_key="bat_config_capacity",
        native_unit_of_measurement="Ah",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-check-outline",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="bat_config_rated_battery_voltage",
        component="battery_config",
        translation_key="bat_config_rated_battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-check-outline",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="bat_config_cell_type",
        component="battery_config",
        translation_key="bat_config_cell_type",
        device_class=SensorDeviceClass.ENUM,
        options=["Lead Acid", "Lithium Iron Phosphate", "Ternary", "Lithium Titanate", "Agm", "Gel", "Flooded"],
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-check-outline",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="bat_config_eps_buffer",
        component="battery_config",
        translation_key="bat_config_eps_buffer",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-check-outline",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="bat_config_address_2",
        component="battery_config",
        translation_key="bat_config_address_2",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-check-outline",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="bat_config_address_3",
        component="battery_config",
        translation_key="bat_config_address_3",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-check-outline",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="bat_config_address_4",
        component="battery_config",
        translation_key="bat_config_address_4",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-check-outline",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="bat_config_tempco",
        component="battery_config",
        translation_key="bat_config_tempco",
        native_unit_of_measurement="mV/Cell",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-check-outline",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="bat_config_voltage_float",
        component="battery_config",
        translation_key="bat_config_voltage_float",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:battery-check-outline",
        entity_registry_enabled_default=False,
    ),
    SofarSensorDescription(
        key="sync_rtc_result",
        component="rtc_sync",
        translation_key="sync_rtc_result",
        device_class=SensorDeviceClass.ENUM,
        options=[
            "Successful",
            "Operation In Progress",
            "Enabled Discharging",
            "Disabled",
            "Operation Failed Controller Refused To Respond",
            "Operation Failed No Response From The Controller",
            "Operation Failed Current Function Disabled",
            "Operation Failed Parameter Access Failed",
            "Operation Failed Input Parameters Incorrect",
        ],
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)
