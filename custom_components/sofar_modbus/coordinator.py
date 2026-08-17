"""DataUpdateCoordinator wrapping SofarInverter.async_update_readings()/async_update_settings().

sofar_modbus reads each polled component independently and contains a failed
one in its returned UpdateReport rather than failing the whole poll — only a
dead link (ModbusConnectionError) still raises. This coordinator builds on
that in two ways solax_modbus's production behavior showed were still
missing (see the design note this ships alongside):

- A component that fails gets one retry before it's accepted as failed,
  mirroring solax_modbus's transport-level `retries=1` one layer up —
  modbus_connection deliberately disables backend retries (see its own
  commit 115df8b) so a failure surfaces on the first attempt and "the
  wrapper alone decides what happens next"; this coordinator is that
  wrapper.
- The settings tier is read only every _SLOW_TIER_EVERY_N_CYCLES-th cycle; readings every cycle.

Also disconnect()s after repeated timed-out polls to recover a link
that's up but unresponsive (a wedged serial-to-network bridge), and stores
the report as coordinator.data so entities can tell which of them, if any,
went stale.
https://home-assistant-libs.github.io/modbus-connection/home-assistant/integration/

``pending`` backs the number/select/switch write entities whose registers the
device only accepts as one combined block (FeedIn limitation, active power
control): those entities stage a value here instead of writing it, and a
paired button entity performs the actual write and clears the keys it just
committed. See pending_or_live().
"""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Any, TypeVar, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from modbus_connection import ModbusConnection, ModbusConnectionError, ModbusError, ModbusTimeoutError

from sofar_modbus.model import UpdateReport  # the PyPI library, not a self-import — see __init__.py
from sofar_modbus.modern.device import SofarInverter

_LOGGER = logging.getLogger(__name__)

_TIMEOUT_DISCONNECT_THRESHOLD = 3
_SLOW_TIER_EVERY_N_CYCLES = 12  # ~60s at the 5s base scan interval
_HEALTH_WINDOW = 60  # ~5min at the 5s base scan interval

type SofarConfigEntry = ConfigEntry[SofarDataUpdateCoordinator]

_T = TypeVar("_T")


class SofarDataUpdateCoordinator(DataUpdateCoordinator[UpdateReport]):
    """Polls one Sofar inverter's components, tiered by how often they change."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SofarConfigEntry,
        connection: ModbusConnection,
        device: SofarInverter,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=entry.title,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.connection = connection
        self.device = device
        self._consecutive_timeouts = 0
        self._consecutive_failures: dict[str, int] = {}
        self._poll_outcomes: deque[bool] = deque(maxlen=_HEALTH_WINDOW)
        self.last_error: str | None = None
        self.last_error_time: datetime | None = None
        self._cycle = 0
        self._force_slow_tier = True  # first refresh covers both tiers — served_components needs the full picture
        self.pending: dict[str, Any] = {}

    @property
    def success_rate(self) -> float | None:
        """Percent of the last `_HEALTH_WINDOW` poll cycles with no failed component.

        None until the first poll lands. Whole-device, not per-component: a
        cycle only counts as a failure if a component's poll still shows up
        in the returned report.failed after _retry_failed's one retry.
        """
        if not self._poll_outcomes:
            return None
        return round(100 * sum(self._poll_outcomes) / len(self._poll_outcomes), 1)

    def _record_poll_outcome(self, success: bool, error: ModbusError | None) -> None:
        self._poll_outcomes.append(success)
        if error is not None:
            self.last_error = f"{type(error).__name__}: {error}"
            self.last_error_time = dt_util.utcnow()

    @property
    def served_components(self) -> frozenset[str]:
        """All component names served by this inverter type. Empty until the first refresh lands."""
        if self.data is not None:
            return frozenset(self.data.updated | set(self.data.failed))
        return frozenset()

    def pending_or_live(self, key: str, live_value: _T) -> _T:
        """What a staged number/select/switch entity should show right now.

        The value the user last set this session, if any and if it hasn't
        been committed yet — otherwise whatever the last successful poll
        read. In-memory only: these registers are volatile on the device
        itself (no flash wear from writing them often), so there's nothing
        to persist across a restart either.
        """
        return cast("_T", self.pending.get(key, live_value))

    async def async_request_refresh(self) -> None:
        self._force_slow_tier = True
        await super().async_request_refresh()

    async def _async_update_data(self) -> UpdateReport:
        try:
            report = await self.device.async_update_readings()
            if not self.device.inverter_type:
                # Still unrecognized — placeholder success so __init__.py's own check raises a clear error.
                return UpdateReport(updated={"identity"}, failed={})
            if self._force_slow_tier or (self._cycle > 0 and self._cycle % _SLOW_TIER_EVERY_N_CYCLES == 0):
                self._force_slow_tier = False
                settings_report = await self.device.async_update_settings()
                report = UpdateReport(
                    report.updated | settings_report.updated,
                    {**report.failed, **settings_report.failed},
                )
            self._cycle += 1
            report = await self._retry_failed(report)
            if not report.updated:
                errors = list(report.failed.values())
                self._record_poll_outcome(False, errors[0] if errors else None)
                if not errors:
                    raise UpdateFailed(f"{self.name}: no component answered")
                cause = errors[0] if len(errors) == 1 else ExceptionGroup("all components failed to refresh", errors)
                raise UpdateFailed(f"{self.name}: no component answered: {errors[0]}") from cause
            self._consecutive_timeouts = 0
            self._record_poll_outcome(not report.failed, next(iter(report.failed.values()), None))
            return report
        except ModbusTimeoutError as err:
            self._consecutive_timeouts += 1
            if self._consecutive_timeouts >= _TIMEOUT_DISCONNECT_THRESHOLD:
                _LOGGER.debug(
                    "%s: %d consecutive timed-out polls, recycling the connection",
                    self.name,
                    self._consecutive_timeouts,
                )
                await self.connection.disconnect()
                self._consecutive_timeouts = 0
            self._record_poll_outcome(False, err)
            raise UpdateFailed(str(err)) from err
        except ModbusError as err:
            # ModbusConnectionError (dead link) reaches here,
            # while per-block failures once alive are contained in UpdateReport.failed.
            self._record_poll_outcome(False, err)
            raise UpdateFailed(str(err)) from err

    async def _retry_failed(self, report: UpdateReport) -> UpdateReport:
        """Give every failed component one more try before accepting the failure.

        Skipped when nothing answered on the first pass (e.g. an all-timeout
        outage) to avoid doubling the timeout latency when the link is down.
        """
        if report.failed and report.updated:
            updated: set[str] = set()
            failed: dict[str, ModbusError] = {}
            for name in report.failed:
                try:
                    await getattr(self.device, name).async_update()
                except ModbusConnectionError:
                    raise
                except ModbusError as err:
                    failed[name] = err
                else:
                    updated.add(name)
            report = UpdateReport(report.updated | updated, failed)

        for name, cause in report.failed.items():
            prev = self._consecutive_failures.get(name, 0)
            self._consecutive_failures[name] = prev + 1
            if prev == 0:
                _LOGGER.warning(
                    "%s: %s failed to refresh and is keeping its previous values: %s",
                    self.name,
                    name,
                    cause,
                )
        for name in report.updated:
            self._consecutive_failures.pop(name, None)

        return report
