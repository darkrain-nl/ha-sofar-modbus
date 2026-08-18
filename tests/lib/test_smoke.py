"""Smoke test: setup -> SofarInverter -> entity filtering -> read every served
field, against the mock backend, for both a PV-only and a HYBRID identity.

The end-to-end check sensor.py's SENSOR_DESCRIPTIONS component mapping is
validated against during development. Safe to run standalone:
`python tests/lib/test_smoke.py`.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# sensor.py has package-relative imports (.coordinator, .entity), so unlike
# the old standalone generated_sensors.py it can't be loaded as a bare
# top-level module — import it as part of custom_components.sofar_modbus
# instead, the same way test_coordinator.py/test_diagnostics.py do.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from homeassistant.components.sensor import SensorDeviceClass  # noqa: E402
from modbus_connection import ModbusTimeoutError  # noqa: E402
from modbus_connection.encode import encode_int  # noqa: E402
from modbus_connection.mock import MockModbusConnection  # noqa: E402
from sofar_modbus.model import SofarComponentBase  # noqa: E402
from sofar_modbus.modern.device import SofarInverter  # noqa: E402

from custom_components.sofar_modbus.sensor import SENSOR_DESCRIPTIONS, SofarSensor, SofarTotalSensor  # noqa: E402


def test_all_descriptions_resolve() -> None:
    """Every (component, key) row must resolve to a real attribute on
    SofarInverter, regardless of which identity would poll it — catches a
    wrong entry in generate_sofar_model.py's component mapping that the
    PV/HYBRID runs below wouldn't exercise on their own (e.g. an EPS- or
    MPPT-only field neither test identity serves).
    """
    device = SofarInverter.__new__(SofarInverter)
    SofarInverter.__init__(device, unit=None)  # type: ignore[arg-type]
    bad: list[str] = []
    for description in SENSOR_DESCRIPTIONS:
        component = getattr(device, description.component, None)
        if component is None or not hasattr(component, description.key):
            bad.append(f"{description.component}.{description.key}")
    assert not bad, f"SENSOR_DESCRIPTIONS references missing attributes: {bad}"
    print(f"all {len(SENSOR_DESCRIPTIONS)} sensor rows resolve to real attributes")


def _seed_serial(unit: object, serial: str) -> dict[int, int]:
    regs: dict[int, int] = {}
    padded = serial.ljust(14, "\x00")
    for i in range(7):
        hi, lo = ord(padded[2 * i]), ord(padded[2 * i + 1])
        regs[0x445 + i] = (hi << 8) | lo
    unit.load_raw({"holding": regs})  # type: ignore[attr-defined]
    return regs


async def _run(serial: str, label: str) -> int:
    conn = MockModbusConnection()
    unit = conn.for_unit(1)
    seeded = _seed_serial(unit, serial)

    device = SofarInverter(unit)

    # No public "what will be polled" surface — seed every component's
    # fields regardless of whether this inverter type actually serves it;
    # an unpolled component's registers are simply never read.
    all_regs = dict(seeded)
    for comp in vars(device).values():
        if not isinstance(comp, SofarComponentBase):
            continue
        for _name, field in type(comp).declared_fields.items():
            addr = getattr(field, "address", None)
            if addr is not None:
                all_regs.setdefault(addr, 1)
    unit.load_raw({"holding": all_regs})

    report = await device.async_update()
    assert report.complete, f"unexpected failures against the mock backend: {report.failed}"
    print(f"[{label}] serial={device.serial_number} model={device.model} type={device.inverter_type!r}")
    served = report.updated | set(report.failed)  # every component this poll attempted

    built = 0
    skipped = 0
    for description in SENSOR_DESCRIPTIONS:
        if description.component not in served:
            skipped += 1
            continue
        component = getattr(device, description.component)
        getattr(component, description.key)  # must not raise
        built += 1
    print(f"[{label}] {built} entities built, {skipped} skipped (unserved by this inverter type)")
    assert built > 0
    return built


async def test_enum_sensor_renders_as_text() -> None:
    """system_state is IntEnum-backed (SystemState); native_value must show a
    label like "Grid Connected", not a bare int — Python 3.11 changed
    IntEnum.__str__ to print just the number, which is what the frontend
    showed before device_class=ENUM/options were wired up. Regression guard
    for that wiring in sensor.py and generate_sofar_model.py.
    """
    unit = MockModbusConnection().for_unit(1)
    _seed_serial(unit, "SS2ES104N5S445")
    unit.holding[0x0404] = 2  # system_state -> GRID_CONNECTED

    device = SofarInverter(unit)
    report = await device.async_update()
    assert "state" in report.updated, f"system_state's component did not refresh: {report.failed}"

    coordinator = SimpleNamespace(device=device, config_entry=SimpleNamespace(title="Test Sofar"))
    description = next(d for d in SENSOR_DESCRIPTIONS if d.key == "system_state")
    entity = SofarSensor(coordinator, description)  # type: ignore[arg-type]
    assert entity.native_value == "Grid Connected", f"expected a text label, got {entity.native_value!r}"
    print("enum-sensor-renders-as-text: PASSED")


async def test_apparent_power_sensor_scales_kva_register_to_va_unit() -> None:
    """apparent_power_output_total's register is kVA-scaled (sofar-modbus 0.1.11), but HA's
    UnitOfApparentPower has no kilo variant — SofarSensorDescription.scale must convert it to
    match the declared VA unit. Regression guard for that scale=1000 wiring.
    """
    unit = MockModbusConnection().for_unit(1)
    _seed_serial(unit, "SS2ES104N5S445")
    unit.holding[0x0487] = 500  # apparent_power_output_total: 500 * 0.01 = 5.0 kVA

    device = SofarInverter(unit)
    report = await device.async_update()
    assert "grid" in report.updated, f"grid component did not refresh: {report.failed}"

    coordinator = SimpleNamespace(device=device, config_entry=SimpleNamespace(title="Test Sofar"))
    description = next(d for d in SENSOR_DESCRIPTIONS if d.key == "apparent_power_output_total")
    entity = SofarSensor(coordinator, description)  # type: ignore[arg-type]
    assert entity.native_value == 5000.0, f"expected 5.0 kVA scaled to 5000.0 VA, got {entity.native_value!r}"
    print("apparent-power-sensor-scales-kva-register-to-va-unit: PASSED")


def test_reactive_power_sensors_declare_kilovar_unit() -> None:
    """reactive_power_* sensors must declare kvar, not var — see sofar-modbus 0.1.11 (9cc434d).
    Regression guard for the sensor.py unit fix, independent of the library's own field metadata.
    """
    from homeassistant.const import UnitOfReactivePower

    reactive = [d for d in SENSOR_DESCRIPTIONS if d.device_class == SensorDeviceClass.REACTIVE_POWER]
    assert reactive, "expected at least one reactive_power sensor description"
    assert all(d.native_unit_of_measurement == UnitOfReactivePower.KILO_VOLT_AMPERE_REACTIVE for d in reactive), (
        f"a reactive_power sensor still declares the base var unit: {[d.key for d in reactive if d.native_unit_of_measurement != UnitOfReactivePower.KILO_VOLT_AMPERE_REACTIVE]}"
    )
    print("reactive-power-sensors-declare-kilovar-unit: PASSED")


def test_offgrid_loadpeakratio_is_not_apparent_power() -> None:
    """offgrid_loadpeakratio* is a dimensionless per-unit ratio, not apparent power — see
    sofar-modbus 0.1.11 (96d1714). Regression guard for the sensor.py device_class/unit fix.
    """
    loadpeakratio = [d for d in SENSOR_DESCRIPTIONS if d.key.startswith("offgrid_loadpeakratio")]
    assert loadpeakratio, "expected at least one offgrid_loadpeakratio sensor description"
    assert all(d.device_class is None and d.native_unit_of_measurement is None for d in loadpeakratio), (
        f"an offgrid_loadpeakratio sensor still declares apparent-power device_class/unit: {[d.key for d in loadpeakratio]}"
    )
    print("offgrid-loadpeakratio-is-not-apparent-power: PASSED")


async def test_total_increasing_dip_guard() -> None:
    """load_consumption_total is total_increasing; a small device-side torn-read
    dip must be held at the last good value, but a genuine large drop (a daily
    counter's midnight reset) must pass straight through. Regression guard for
    SofarSensor._smoothed_total_increasing.
    """
    unit = MockModbusConnection().for_unit(1)
    _seed_serial(unit, "SS2ES104N5S445")

    def _set_load_consumption_total(kwh: float) -> None:
        raw = round(kwh / 0.1)
        for addr, word in zip(range(0x068A, 0x068C), encode_int(raw, count=2), strict=True):
            unit.holding[addr] = word

    _set_load_consumption_total(17506.4)
    device = SofarInverter(unit)
    report = await device.async_update()
    assert "energy" in report.updated, f"energy component did not refresh: {report.failed}"

    coordinator = SimpleNamespace(device=device, config_entry=SimpleNamespace(title="Test Sofar"))
    description = next(d for d in SENSOR_DESCRIPTIONS if d.key == "load_consumption_total")
    entity = SofarTotalSensor(coordinator, description)  # type: ignore[arg-type]
    assert entity.native_value == 17506.4, f"expected the seeded value, got {entity.native_value!r}"

    _set_load_consumption_total(17506.3)  # a ~0.0006% dip -> torn-read noise
    await device.async_update()
    assert entity.native_value == 17506.4, f"a small dip should be held, got {entity.native_value!r}"

    _set_load_consumption_total(17506.5)  # a normal increase
    await device.async_update()
    assert entity.native_value == 17506.5, f"a real increase should pass through, got {entity.native_value!r}"

    _set_load_consumption_total(0.0)  # a genuine reset (e.g. daily counter at midnight)
    await device.async_update()
    assert entity.native_value == 0.0, f"a genuine reset should pass through, got {entity.native_value!r}"
    print("total-increasing-dip-guard: PASSED")


async def test_total_increasing_holds_available_through_failed_poll() -> None:
    """A total_increasing counter must stay available (and keep its last
    value) when its own component fails to refresh this poll — same
    principle as the dip guard, applied to the failure axis: overnight
    read trouble shouldn't flap the energy dashboard's sensors unavailable
    every few minutes. A plain measurement sensor (grid_frequency) must
    still go unavailable when its component fails, so the override doesn't
    leak beyond TOTAL_INCREASING. Regression guard for
    SofarSensor.available / SofarEntity._link_available.
    """
    unit = MockModbusConnection().for_unit(1)
    _seed_serial(unit, "SS2ES104N5S445")
    unit.holding[0x0484] = 5000  # grid_frequency

    device = SofarInverter(unit)
    report = await device.async_update()
    assert report.complete, f"unexpected failures against the mock: {report.failed}"

    energy_description = next(d for d in SENSOR_DESCRIPTIONS if d.key == "load_consumption_total")
    grid_description = next(d for d in SENSOR_DESCRIPTIONS if d.key == "grid_frequency")

    def _coordinator(current_report: object) -> Any:
        return SimpleNamespace(
            device=device, config_entry=SimpleNamespace(title="Test Sofar"), data=current_report, last_update_success=True
        )

    energy_entity = SofarTotalSensor(_coordinator(report), energy_description)
    grid_entity = SofarSensor(_coordinator(report), grid_description)
    assert energy_entity.available and grid_entity.available, "both should be available after a clean poll"
    held_value = energy_entity.native_value

    unit.fail_read(
        0x0684, ModbusTimeoutError("simulated overnight dropout")
    )  # solar_generation_today's block, same 'energy' component
    energy_failed_report = await device.async_update()
    assert "energy" in energy_failed_report.failed, f"expected energy to fail this poll: {energy_failed_report.updated}"
    unit.fail_read(0x0684, None)  # clear it before the next poll

    energy_entity = SofarTotalSensor(_coordinator(energy_failed_report), energy_description)
    assert energy_entity.available, "a total_increasing sensor must hold available through its own component's failure"
    assert energy_entity.native_value == held_value, "it must keep reporting the last known value, not go blank"

    unit.fail_read(0x0484, ModbusTimeoutError("simulated overnight dropout"))  # grid_frequency's own block
    grid_failed_report = await device.async_update()
    assert "grid" in grid_failed_report.failed, f"expected grid to fail this poll: {grid_failed_report.updated}"

    grid_entity = SofarSensor(_coordinator(grid_failed_report), grid_description)
    assert not grid_entity.available, "a plain measurement sensor must still go unavailable on its own component's failed poll"

    dead_link_coord: Any = SimpleNamespace(
        device=device, config_entry=SimpleNamespace(title="Test Sofar"), data=energy_failed_report, last_update_success=False
    )
    dead_link_entity = SofarTotalSensor(dead_link_coord, energy_description)
    assert dead_link_entity.available, "total_increasing must hold available even when the link is down"

    dead_link_grid_entity = SofarSensor(dead_link_coord, grid_description)
    assert not dead_link_grid_entity.available, "a measurement sensor must go unavailable when the link is down"
    print("total-increasing-holds-available-through-failed-poll: PASSED")


async def test_total_sensor_restores_state_and_seeds_high_water() -> None:
    unit = MockModbusConnection().for_unit(1)
    _seed_serial(unit, "SS2ES104N5S445")
    device = SofarInverter(unit)
    await device.async_update_settings()  # settles serial_number/model/inverter_type, needed for the entity's unique_id

    coordinator: Any = SimpleNamespace(
        device=device,
        config_entry=SimpleNamespace(title="Test Sofar"),
        async_add_listener=lambda *args, **kwargs: lambda: None,
    )
    description = next(d for d in SENSOR_DESCRIPTIONS if d.key == "load_consumption_total")
    entity = SofarTotalSensor(coordinator, description)
    entity.async_on_remove = lambda *args, **kwargs: None  # type: ignore[method-assign]

    # Mock async_get_last_sensor_data
    async def _mock_last_sensor_data() -> Any:
        return SimpleNamespace(native_value="1234.5")

    entity.async_get_last_sensor_data = _mock_last_sensor_data  # type: ignore[method-assign]
    await entity.async_added_to_hass()

    assert entity.native_value == 1234.5, f"expected restored 1234.5, got {entity.native_value!r}"
    assert entity._total_increasing_high_water == 1234.5

    # A torn read slightly below the restored mark is held
    def _set_load_consumption_total_restored(kwh: float) -> None:
        raw = round(kwh / 0.1)
        for addr, word in zip(range(0x068A, 0x068C), encode_int(raw, count=2), strict=True):
            unit.holding[addr] = word

    _set_load_consumption_total_restored(1234.4)
    await device.async_update()
    assert entity.native_value == 1234.5, (
        f"torn read on first poll after restart must be held against restored high water, got {entity.native_value!r}"
    )
    print("total-sensor-restores-state-and-seeds-high-water: PASSED")


async def test_hybrid_serves_meaningfully_more_entities_than_pv() -> None:
    """Regression guard: a PV inverter must end up with meaningfully fewer
    entities than a HYBRID one (battery, EPS, passive mode etc. are
    HYBRID-only), not "nearly everything" for both.
    """
    pv_built = await _run("SS2ES104N5S445", "PV (live hardware serial)")
    hybrid_built = await _run("SP1XXES100XX", "HYBRID (SP1 prefix)")
    assert pv_built < hybrid_built * 0.8, (
        f"PV ({pv_built}) should be well below HYBRID ({hybrid_built}) — component filtering may not be applying"
    )
    print("hybrid-serves-meaningfully-more-entities-than-pv: PASSED")


async def main() -> None:
    test_all_descriptions_resolve()
    await test_enum_sensor_renders_as_text()
    await test_apparent_power_sensor_scales_kva_register_to_va_unit()
    test_reactive_power_sensors_declare_kilovar_unit()
    test_offgrid_loadpeakratio_is_not_apparent_power()
    await test_total_increasing_dip_guard()
    await test_total_increasing_holds_available_through_failed_poll()
    await test_total_sensor_restores_state_and_seeds_high_water()
    await test_hybrid_serves_meaningfully_more_entities_than_pv()
    print("SMOKE TEST PASSED")


if __name__ == "__main__":
    asyncio.run(main())
