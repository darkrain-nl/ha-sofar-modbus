# Changelog

All notable changes to this project are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/) — while the major version is `0`, a `MINOR` bump
means new user-facing capability (a new platform, new entities reachable from Home Assistant),
a `PATCH` bump means a fix with no new capability. Each version bump gets a matching git tag
and GitHub Release. Tags are unprefixed (`X.Y.Z`, matching `manifest.json`'s `version` field
and Home Assistant Core's own tag format) — versions before 0.3.15 were tagged `vX.Y.Z`.

## [0.6.4] - 2026-08-19

### Changed

- **EPS support is now auto-detected**: the config flow no longer asks "Poll EPS / backup
  registers" as a setup checkbox. Setup and reconfigure now always probe the EPS/off-grid
  register block, and `read_eps` is stored from whether that probe actually came back
  (`"eps" in report.updated`) — inverters without EPS wiring get it disabled automatically,
  and inverters that do have it no longer depend on the user knowing to enable it. Ported from
  the `sofar-modbus-init` Core PR's review round (`96027d7722e`), which found the same thing:
  the device already refuses unsupported register blocks gracefully, so asking the user to
  guess was unnecessary. Also carries over a subtlety from that PR: once probed with
  `read_eps=True`, `SofarInverter.inverter_type` always carries the EPS bit, so the
  unrecognized-inverter check in `_async_probe` now calls `identify(serial)` directly instead
  of relying on `inverter_type` truthiness.

## [0.6.3] - 2026-08-18

### Fixed

- `reactive_power_*` sensors (grid and off-grid, totals and per-phase) declared the base `var`
  unit while their register is `0.01`-scaled the same way as the neighboring `kW` rows — actually
  `kvar`, understating every reading by 1000x. Now declare `UnitOfReactivePower.KILO_VOLT_AMPERE_REACTIVE`.
  Confirmed by `sofar-modbus` 0.1.11 ([`9cc434d`](https://github.com/darkrain-nl/sofar-modbus/commit/9cc434d)),
  which fixed the same mislabeling in the library's own field metadata — that fix has no automatic
  effect here, since `sensor.py` declares its own units independently of the library's, so this
  integration needed the matching fix on its own side.
- `apparent_power_*` sensors have the identical bug, but Home Assistant's `UnitOfApparentPower`
  has no kilo variant to switch to — `SofarSensorDescription` gained a `scale` field (default
  `1.0`) and these descriptions now set `scale=1000` to convert the kVA-scaled register into the
  VA the declared unit expects, applied in `SofarSensor.native_value`.
- `offgrid_loadpeakratio*` was declared as apparent power (`device_class=APPARENT_POWER`,
  `unit=VA`) but is actually a dimensionless per-unit ratio, unrelated to apparent power. Dropped
  the device_class and unit, matching how `power_factor_*` (also dimensionless) is already
  declared. Confirmed by `sofar-modbus` 0.1.11
  ([`96d1714`](https://github.com/darkrain-nl/sofar-modbus/commit/96d1714)).
- These three fixes are targeted hand-edits to `sensor.py`'s generated `SENSOR_DESCRIPTIONS`
  block, each with an inline comment — deliberate, verified deviations from upstream
  `plugin_sofar.py` rather than a full resync, so a future resync doesn't silently revert them
  without someone noticing.

### Changed

- Bumped `sofar-modbus` to 0.1.11 (was 0.1.10). No code changes needed for the bump itself — the
  library's only other 0.1.11 changes are to its own `script/query.py` diagnostic tool, which this
  integration doesn't use.

### Verification

- `pytest -q` — full suite (81 passed, including three new regression tests for the fixes above).
- `python tests/lib/test_coordinator.py`, `test_smoke.py`, `test_write_entities.py`,
  `test_diagnostics_lib.py` — all passed.
- `ruff check` clean. `mypy --explicit-package-bases custom_components/ tests/` clean.

## [0.6.2] - 2026-08-17

### Changed

- Bumped `sofar-modbus` to 0.1.10 (was 0.1.8). That release replaced the library's flat
  polled-component list with a native readings/settings split
  (`async_update_readings()`/`async_update_settings()`) and removed `SofarInverter.prime()`,
  `async_setup()`, and `polled_components`. `coordinator.py` now polls the readings tier every
  cycle and the settings tier on the existing `_SLOW_TIER_EVERY_N_CYCLES` cadence, trusting the
  library's own split as the tier boundary — this supersedes the previous entry below:
  `_VOLATILE_COMPONENTS` (the hand-maintained frozenset added there) and its drift-guard test are
  gone now that the library provides the split natively. `energy`/`battery_energy` move from the
  old slow tier into the readings tier as a result (they're telemetry, not settings, in the
  library's split) — polled every cycle instead of every ~60s; the recorder already buckets their
  long-term statistics at 5 minutes regardless, so this doesn't change what users see, only
  register traffic.
- `served_components` (and so which entities each platform creates) is now derived from the
  coordinator's last completed poll rather than a static per-inverter-type list — the library no
  longer exposes one without actually polling. `__init__.py` now always blocks on
  `async_config_entry_first_refresh()` (forcing both tiers on that first poll) before forwarding
  platforms, instead of skipping ahead to entity creation for a pre-identified device and
  refreshing in the background afterward. `config_flow.py`'s probe similarly moved from a bare
  identity read to a full `async_update()`, since the library no longer offers a lighter option.
- Bumped `modbus-connection` to 4.8.1 (was 4.7.0). Non-breaking — the intervening releases were a
  serial-transport timeout fix (this integration is TCP-only) and typing-only overload additions.

### Behavior change

- A previously-configured inverter that's unreachable at boot (e.g. a PV-only unit asleep in the
  dark) now retries setup (`SETUP_RETRY`) instead of loading immediately with unavailable
  entities. This is a side effect of the `sofar-modbus` 0.1.10 changes above, not a deliberate
  choice: the library no longer offers a way to learn a pre-identified device's served components
  without polling it, so entity creation can no longer happen before that first poll completes.
  Restoring the old behavior would need a `sofar-modbus` release of its own, which wasn't pursued
  given the open PR staging this integration for Home Assistant Core. Home Assistant's own
  setup-retry backoff still recovers once the device answers.

### Verification

- `pytest -q` — full suite (78 passed).
- `python tests/lib/test_coordinator.py`, `test_smoke.py`, `test_write_entities.py`,
  `test_diagnostics_lib.py` — all passed.
- `ruff check` clean. `mypy --explicit-package-bases custom_components/ tests/` (CI's invocation)
  clean.

## [0.6.1] - 2026-08-16

### Fixed

- The `button.*_sync_clock` entity added in 0.6.0 only appeared on HYBRID inverters. Upstream
  `plugin_sofar.py` allows the underlying "Update System Time" write on `HYBRID | PV` — only its
  separate read-back result sensor (`sync_rtc_result`) is HYBRID-only. The button is now gated on
  the inverter being PV or HYBRID directly, matching upstream, instead of on the HYBRID-only
  sensor's component. PV inverters get the button with no confirmation sensor afterwards, same as
  upstream.

### Verification

- `pytest -q` — full suite (79 passed), including new PV-inverter coverage for the button.
- `ruff check` / `mypy` clean on changed files.

## [0.6.0] - 2026-08-16

### Added

- A `button.*_sync_clock` entity on HYBRID inverters that writes the current local time to the
  inverter's clock via the library's `async_set_time()`. Fire-and-forget: the existing
  `sync_rtc_result` diagnostic sensor reports whether it took on the next poll.

### Verification

- `pytest tests/test_entity_shape.py tests/test_controls.py -q` and the full suite (79 passed).
- `ruff check` / `mypy` clean on changed files.

## [0.5.3] - 2026-08-16

### Fixed

- `select.py`'s `_CHARGER_USE_MODE_OPTIONS` only mapped 6 of the library's 8 `ChargerUseMode`
  values — a HYBRID inverter reporting `GENERATOR_MODE` or `FEED_IN_PRIORITY_MODE` would raise
  an uncaught `KeyError` in `current_option`, surfacing as the entity going unavailable. Found
  during a full manual audit of every entity this integration creates, cross-checked against
  the pinned `sofar-modbus` library's actual enum values.
- 9 `sensor.py` rows were missing a `device_class` their unit and sibling entities already
  called for: 6 `bat_config_*` voltage sensors → `VOLTAGE`, 2 `bat_config_*` current-limit
  sensors → `CURRENT`, `waiting_time` → `DURATION`.
- `battery_capacity_1` through `_8` were missing `device_class=BATTERY`, unlike their aggregate
  sibling `battery_capacity_total` which already set it for the identical concept.
- The `_total` side of all 6 `_today`/`_total` energy sensor pairs (`solar_generation`,
  `load_consumption`, `import_energy`, `export_energy`, `battery_input_energy`,
  `battery_output_energy`) was missing `suggested_display_precision=2`, unlike its `_today`
  sibling.
- `bat_config_charging_voltage` was the only one of 19 `bat_config_*` sensors not disabled by
  default (`entity_registry_enabled_default=False`).

### Added

- `tests/test_entity_shape.py` — permanent structural checks so these bug classes can't
  silently regress: every `device_class=ENUM` sensor's `options` must match `_enum_label()`
  over its underlying library enum, every hand-maintained `select.py` option dict must cover
  its library enum's full member set (this is what would have caught the `ChargerUseMode` bug
  above), and every `translation_key` in use must resolve in `strings.json` with
  `translations/en.json` kept byte-identical.

### Removed

- The `scripts/` folder (`generate_sofar_model.py`, `extract_sofar_ast.py`) — regenerating the
  `sensor.py` sensor description block from upstream `plugin_sofar.py` is now done by asking a
  coding agent to resync it, rather than running a standalone generator script. See README's
  "The register map is generated" section.

### Verification

- Full suite (`uv run pytest` — 79 passed), `ruff check`, `ruff format --check`, and
  `mypy --explicit-package-bases custom_components/ tests/` all green on `77b1d04`.
- `tests/test_entity_shape.py`'s select-completeness test confirmed to fail against the
  pre-fix `ChargerUseMode` map, verifying it actually catches the bug it targets.
- `strings.json`/`translations/en.json` confirmed byte-identical and valid JSON.

## [0.5.2] - 2026-08-15

### Changed

- Stopped reaching into `sofar-modbus`'s private `_polled` attribute, now that it exposes
  `polled_components` publicly.

### Verification

- Full suite (`pytest tests/` — 73 passed), `ruff check`, `ruff format --check`, and
  `mypy --explicit-package-bases custom_components/ tests/` all green on `48f6801`.

## [0.5.1] - 2026-08-15

### Fixed

- **Communication Health Entities Went Unavailable During a Dead Link**: `communication_health`
  and its three sibling entities (`_success_rate`, `_last_error`, `_last_error_time`, added in
  0.5.0) inherited `CoordinatorEntity`'s default `available`, which tracks
  `coordinator.last_update_success` — the same flag `SofarEntity`-based sensors use, but those
  gate on a per-component failure while this one only goes `False` on a dead link (a
  `ModbusConnectionError`/fatal timeout that fails the whole poll). That's exactly the moment
  these entities exist to describe, and `success_rate`/`last_error`/`last_error_time` are still
  recorded correctly on the coordinator when a poll fails outright — the entities were just
  hiding it behind `unavailable`. Fixed by giving the whole family a shared
  `_SofarCommunicationHealthEntity` base (mirroring `SofarEntity`) whose `available` always
  returns `True`, since none of them read the per-component `UpdateReport` in the first place.

### Verification

- New test `test_communication_health_entities_stay_available_on_dead_link` confirms all four
  entities stay `available` with `coordinator.last_update_success = False`. Full suite
  (`pytest tests/` — 72 passed), `ruff check`, `ruff format --check`, and
  `mypy --explicit-package-bases custom_components/ tests/` all green on `afa2924`.

## [0.5.0] - 2026-08-15

### Changed

- **Communication Health Split Into Separate Entities**: `communication_health`'s
  `success_rate`, `last_error`, and `last_error_time` used to live as
  `extra_state_attributes` on that one sensor. Home Assistant Core discourages
  state-like data in entity attributes (not queryable in history/statistics, not usable
  in automations/dashboards as a first-class entity), so each is now its own diagnostic
  sensor entity: `communication_health_success_rate` (percentage, enabled by default),
  `communication_health_last_error` and `communication_health_last_error_time` (disabled
  by default — `None` most of the time, mirroring `rtc`/`hardware_version`'s convention
  for low-traffic diagnostics). `last_error_time`'s underlying value also changed from an
  ISO string to a real timestamp (`SensorDeviceClass.TIMESTAMP`).
  **⚠️ Breaking for automations/dashboards**: anything reading
  `communication_health`'s `success_rate`/`last_error`/`last_error_time` attributes needs
  to point at the three new entities instead; the attributes no longer exist.
  `communication_health` itself (state `good`/`degraded`/`poor`/`unknown`) is unchanged.
  Unrelated to the `diagnostics.py` config-entry diagnostics *download* feature touched
  in `7a3f47a`/0.4.1 — different "diagnostics", not touched here.

### Fixed

- **Off-Grid Output Readings Stuck on the Slow Poll Tier**: `offgrid_single_phase` and
  `offgrid_three_phase` (31 sensors combined with `reactive_power_offgrid_total`:
  voltage/current/active/reactive/apparent power per phase, load-peak-ratio) never
  declared `state_class=MEASUREMENT`, even though each has a `device_class` and unit.
  `coordinator.py`'s `_volatile_components()` keys fast-vs-slow tier off exactly that
  field, so these components — live electrical output, most needed during an outage —
  silently landed on the ~60s slow tier instead of the ~5s fast tier by omission, not
  by any deliberate choice. Also picks up long-term statistics (previously none) and
  removes a redundant round trip per cycle where `offgrid`/`offgrid_three_phase` are
  both due, since they're now on the same tier. Root cause: `sensor.py`'s
  `SENSOR_DESCRIPTIONS` tail is generated from upstream `plugin_sofar.py`, which has
  the same gap — fixed with a local override in `scripts/generate_sofar_model.py`
  rather than hand-patched into the generated file, so a future regeneration can't
  silently drop it. `energy`/`battery_energy` remaining on the slow tier (they're
  `TOTAL_INCREASING`, not `MEASUREMENT`) was reviewed and kept as a deliberate choice —
  the recorder buckets long-term statistics at 5 minutes regardless. `offgrid_frequency`
  was left alone to match every other frequency sensor in the file. Reported in #46.

### Verification

- Full suite (`pytest tests/` — 71 passed), `ruff check`, `ruff format --check`, and
  `mypy --explicit-package-bases custom_components/ tests/` all green on `826a38d`.

## [0.4.1] - 2026-08-15

### Fixed

- **Diagnostics Redaction Gap**: 0.4.0's `serial_number` redaction only covered the named
  field — the same serial number was still sitting unredacted in the diagnostics
  `"registers"` dump, ASCII-encoded across 7 words of the identity block. Since that
  register map is public, anyone attaching a diagnostics file to a GitHub issue was still
  leaking their full serial number. Those registers are now stripped from the dump too.
  Found by inspecting a real diagnostics download from a live install — thanks for
  catching it.

### Verification

- New assertions in `tests/test_diagnostics.py` and `tests/lib/test_diagnostics_lib.py`
  confirm the 7 registers are absent from the dump. Full suite (`pytest` — 70 passed,
  standalone `tests/lib/*.py` scripts, `ruff` format/lint, `mypy`) green on `16d707b`.

## [0.4.0] - 2026-08-15

### Changed

- **Entity Translations**: sensor entities (234 rows) and select-platform options now use
  Home Assistant's `translation_key` mechanism instead of hardcoded English `name=`/option
  text, matching the pattern Core requires for new integrations.
  **⚠️ Breaking for automations/scripts**: select-platform state values changed from
  human-readable text to machine slugs (e.g. `"Enabled - Feed-in limitation"` →
  `enabled_feed_in_limitation`, `"Self Use"` → `self_use`), and the `communication_health`
  sensor's states changed from `Good`/`Degraded`/`Poor`/`Unknown` to lowercase
  (`good`/`degraded`/`poor`/`unknown`). Displayed text in the UI is unchanged — only the
  underlying stored value did. If you have automations, scripts, or dashboard conditions
  matching against the old raw text, update them to the new slugs.
- **Diagnostics Redaction**: the inverter serial number is now redacted in diagnostics
  downloads (they get attached to public GitHub issues). A new `serial_prefix` field keeps
  just the first 10 characters — enough to identify or extend `sofar-modbus`'s
  serial-prefix table from an unrecognized inverter's diagnostics dump — without keeping
  the rest of what is otherwise a unique per-device identifier.
- **Manifest**: `integration_type` corrected from `hub` to `device` — one physical inverter
  per config entry, not a bridge to multiple independent devices.

### Fixed

- **Library Coupling**: replaced a reach-in into `sofar-modbus`'s private
  `SofarInverter._polled` attribute (used to skip a redundant Modbus read on startup for an
  already-identified device) with its new public `SofarInverter.prime()` method. Requires
  `sofar-modbus>=0.1.7`.

### Verification

- Full suite (`pytest` — 70 passed, including `tests/lib/test_smoke.py` newly wired into
  pytest collection — standalone `tests/lib/*.py` scripts, `ruff` format/lint, `mypy`) green
  on `a0f6099`, verified against the real released `sofar-modbus==0.1.7` from PyPI.

## [0.3.16] - 2026-08-15

### Changed

- **Faster Default Scan Cadence**: `DEFAULT_SCAN_INTERVAL` (fast tier) drops from 15s to
  5s; `_SLOW_TIER_EVERY_N_CYCLES` and `_HEALTH_WINDOW` are retuned (4→12, 20→60) to hold
  the slow tier at ~60s and the health window at ~5min in wall-clock terms rather than
  quietly shrinking 3x along with the base interval. User-configurable polling was
  considered and dropped — Home Assistant Core's architecture guidance is against exposing
  scan interval as a config option, so this is a fixed retune instead. 5s was chosen over
  `solax_modbus`'s more aggressive 1s/2s/5s tiers because it's empirically verified against
  production: recorder history for a live PV power sensor at a 5s `solax_modbus`
  scan_interval showed exact 5.0s-multiple gaps between state changes (confirming no
  faster internal update was being missed) and a 44% distinct-value rate between
  consecutive polls even in flat conditions (confirming the register isn't just handing
  back a cached duplicate) — matching `solax_modbus`'s own documented floor of "do not
  poll quicker than 5s on inverters with a baud of 9600." Sub-5s cadence remains
  unverified (no bench setup faster than the household's live inverter to test against),
  so it isn't pursued.
  - A third "read once at setup, never repoll" tier for `identity` (serial number,
    firmware versions — genuinely static) was considered and rejected: the upstream
    `sofar_modbus` library's `Identity` component is a single Modbus register block that
    bundles those fields with the inverter's live RTC (the disabled-by-default `System
    Time` sensor), so pulling the whole component off the timer would freeze that sensor's
    value forever if a user ever enabled it. Splitting the two would require an upstream
    `sofar_modbus` change; not pursued given the negligible cost of re-reading ~10 extra
    registers every 60s.

### Verification

- `tests/lib/test_coordinator.py` updated to derive its off-cycle loop count from
  `_SLOW_TIER_EVERY_N_CYCLES` instead of a hardcoded `3`/`N=4`, so it stays correct at the
  new cadence. Full suite (standalone scripts + `pytest`, 64 passed), `ruff` format/lint,
  and `mypy` all green.

## [0.3.15] - 2026-08-15

### Added

- **Communication Health Sensor**: New diagnostic sensor (`sensor.<inverter>_communication_health`) surfacing link quality at a glance — state (`Good`/`Degraded`/`Poor`/`Unknown`) plus `success_rate`, `last_error`, and `last_error_time` attributes, computed from a rolling window of the last 20 poll cycles. Mirrors what `solax_modbus`'s `sensor.sofar_sofar_communication_health` already provides, minus the register-level quarantine engine this integration deliberately doesn't have (see 0.1.8/0.3.3).

### Verification

- New tests in `tests/lib/test_coordinator.py` (`success-rate-reflects-mixed-outcomes`, `health-window-caps-and-drops-oldest`, `last-error-is-recorded-and-not-cleared`) and `tests/test_sensor.py` (`test_communication_health_sensor`).
- All standalone test suites (`test_smoke.py`, `test_coordinator.py`, `test_diagnostics_lib.py`, `test_write_entities.py`), `pytest` (64 passed), `ruff` formatting/linting, and `mypy` checks pass.

## [0.3.14] - 2026-08-14

### Fixed

- **Instant Non-Blocking Startup**: Because inverter type and registers are pre-identified from the config entry unique ID, `async_setup_entry` no longer blocks Home Assistant startup waiting for offline Modbus timeouts (#37).
- **Energy Totals Restored Across Nighttime Restarts**: All entity platforms are registered immediately on boot even if the inverter is sleeping in the dark, allowing `RestoreSensor` (`SofarTotalSensor`) to instantly restore previous long-term energy totals (`total_generation`, `today_generation`) from Home Assistant's database (#37).
- **Eliminate Startup Banner**: Eliminates the 10-second startup latency and "Wrapping up startup" frontend banner on boot (#37).

### Verification

- CI (`ruff`, `mypy`, `pytest`) green on `025476d`.

## [0.3.13] - 2026-08-14

### Added

- **Reconfigure Flow**: Support modifying network connection settings (`host`, `port`, `modbus_addr`, `read_eps`) directly in the Home Assistant UI without having to delete and re-add the inverter integration (#34).
- **Test Suite**: Comprehensive integration and unit test suite achieving 100% test coverage across all platforms (#34).

### Changed

- **Translation Keys**: Migrated `select`, `number`, `switch`, and `button` platforms from hardcoded names to standard `translation_key` with localization in `strings.json` and `translations/en.json` (#34).
- **Logging Privacy**: Redacted inverter serial numbers from coordinator name prefixes, update failure messages, and setup exception messages to protect hardware privacy in logs (#35).
- **Reduced Log Noise**: Downgraded connection recycling on repeated timeouts from warning to debug, preventing warning logs when inverters go to sleep overnight (#35).

### Verification

- CI (`ruff`, `mypy`, `pytest`) green on `2f29154`.

## [0.3.12] - 2026-08-14

### Changed

- Settle startup latency and prevent blocking Home Assistant startup: initial coordinator refresh now polls fast measurements + identity (<1s), allowing HA setup to complete immediately and dismiss the startup banner without delay. An unblocked background task (`entry.async_create_background_task`) immediately refreshes the slow tier (controls, settings, energy totals) in the background.

### Verification

- All standalone test suites (`test_smoke.py`, `test_coordinator.py`, `test_diagnostics.py`, `test_write_entities.py`), `pytest` (28 passed), `ruff` formatting/linting, and `mypy` checks pass.

## [0.3.11] - 2026-08-14

### Changed

- Initial coordinator refresh (cycle 0) on startup now polls all served components for this inverter, populating identity diagnostics (serial number, firmware version) and controls (feed-in limit, active power control, remote switch, charger mode) immediately on boot with no 60-second delay. Subsequent cycles continue to poll the fast tier every 15s and slow tier every 60s.

### Verification

- All standalone test suites (`test_smoke.py`, `test_coordinator.py`, `test_diagnostics.py`, `test_write_entities.py`), `pytest` (28 passed), `ruff` formatting/linting, and `mypy` checks pass.

## [0.3.10] - 2026-08-14

### Changed

- Inverter identity (serial number, model, inverter type) is now initialized in-memory directly from `entry.unique_id` on startup (0ms, zero I/O), allowing coordinator fast/slow tiers and entity platforms to settle immediately before polling starts (#30).

### Verification

- All standalone test suites (`test_smoke.py`, `test_coordinator.py`, `test_diagnostics.py`, `test_write_entities.py`), `pytest` (28 passed), `ruff` formatting/linting, and `mypy` checks pass.

## [0.3.9] - 2026-08-14

### Fixed

- Counted the wrong polls in `coordinator.py` for connection recycling: `_retry_failed` incremented `_consecutive_timeouts` on partial component failures where the link was demonstrably fine (recycling the connection every 3 polls on a slow register), while fatal timeouts where the whole link was wedged raised out of `_poll` and bypassed `_retry_failed` entirely. Timeout counting and threshold disconnects now trigger on fatal `ModbusTimeoutError` in `_async_update_data`, and `_consecutive_timeouts` is reset to 0 on any poll that succeeds (#26).

### Verification

- All standalone test suites (`test_smoke.py`, `test_coordinator.py`, `test_diagnostics.py`, `test_write_entities.py`), `pytest` (27 passed), `ruff` formatting/linting, and `mypy` checks pass.

## [0.3.8] - 2026-08-14

### Changed

- Initial coordinator refresh on startup now polls only the **fast tier** (volatile measurement components like `grid`, `state`, `pv_1_2`, `battery_1_2`, `battery_totals`) instead of all 16–18 components across both fast and slow tiers, cutting blocking startup time from ~11.8s down to ~1–1.5s (~85% reduction).
- Entity platforms (`sensor.py`, `select.py`, `number.py`, `switch.py`, `button.py`, `diagnostics.py`) now discover served entities directly from `coordinator.served_components` (resolved via `device.async_setup()` on the serial number register `0x0445`) rather than requiring a full initial poll across slow registers.
- `_async_probe` in `config_flow.py` now uses `await device.async_setup()` to retrieve model and serial numbers in ~100ms instead of polling the entire register map.

### Verification

- All standalone test suites (`test_smoke.py`, `test_coordinator.py`, `test_diagnostics.py`, `test_write_entities.py`), `pytest` (26 passed), `ruff` formatting/linting, and `mypy` checks pass.

## [0.3.7] - 2026-08-14

### Changed

- Bumped `sofar-modbus` dependency to `>=0.1.6,<0.2.0` (#20).
- `coordinator.py` now aligns with `sofar-modbus` 0.1.6's fatal timeout behavior on silent inverters: if a component times out before any component has answered or refused, `_poll()` aborts immediately rather than walking the remaining components paying $N \times \text{timeout}$ latency. Refusal responses (exception codes) or timeouts occurring after at least one component has responded continue to be contained within `UpdateReport.failed` (#20).

### Verification

- All standalone test suites (`test_smoke.py`, `test_coordinator.py`, `test_diagnostics.py`, `test_write_entities.py`), `pytest` (25 passed), `ruff` formatting/linting, and `mypy` checks pass.

## [0.3.6] - 2026-08-14

### Added

- Bundled official SOFAR brand assets locally in `custom_components/sofar_modbus/brand/` (`icon.png`, `dark_icon.png`, `logo.png`, `dark_logo.png` and `@2x` variants) matching the modern cerulean blue `#366DB0` and aurora cyan `#5EBECA` visual identity. Home Assistant serves these directly for integration and device cards without relying on external CDN brand fetches.

## [0.3.5] - 2026-08-14

### Changed

- Updated `sofar-modbus` requirement to `0.1.5` and bumped `modbus-connection[tmodbus]` floor to `>=4.7.0,<5.0.0` (#6).
- `diagnostics.py` now uses the library's built-in `SofarInverter.async_read_raw()` with `notify=False` rather than hand-iterating components (#3).
- Polling failure logging in `coordinator.py` now emits a `WARNING` only on the initial transition into component failure, suppressing poll spam on subsequent cycles (#7).
- Grouped write/settings components (`feed_in`, `active_power_control`, `passive`, `charger`, `remote`, `eps`) into the slow polling tier, and ensured `async_request_refresh()` immediately polls the slow tier to confirm writes (#5).

### Fixed

- Handled all-timeout outages in `coordinator.py` by raising `UpdateFailed` when no components answer, and skipping retry passes when the first pass had zero responses to prevent doubled timeout latency (#2).
- `TOTAL` and `TOTAL_INCREASING` energy sensors now hold `available = True` unconditionally even through complete link dropouts or nighttime power-downs to avoid gaps in long-term statistics (#1).
- Split totals out into `SofarTotalSensor(RestoreSensor)`, restoring last known sensor data across Home Assistant restarts and seeding the high-water mark for torn-read dip protection immediately (#4).

### Verification

- All standalone test suites (`test_smoke.py`, `test_coordinator.py`, `test_diagnostics.py`, `test_write_entities.py`) and `ruff` lint checks pass.

## [0.3.4] - 2026-08-14

### Changed

- `sofar-modbus` is now installed from PyPI (`sofar-modbus>=0.1.4,<0.2.0`) instead of pinned
  to a `git+https` tag of the `darkrain-nl/sofar-modbus` fork — the library published its
  first PyPI release (`0.1.4`) at
  [pypi.org/project/sofar-modbus](https://pypi.org/project/sofar-modbus/), sourced from the
  same repository this project was already pinned to. `pyproject.toml` and
  `manifest.json`'s `requirements` both updated; no code changes needed — the only change
  between the previously-pinned `v0.1.3` and `0.1.4` is an additive
  `SofarInverter.async_read_raw()` (a device-level equivalent of what this integration's own
  `diagnostics.py` already builds by hand per-component) plus packaging/release-workflow
  commits, nothing this integration's code path touches.

### Verification

- `ruff`/`mypy` clean against the freshly-installed PyPI package; all four `tests/lib/`
  scripts pass unchanged.
- Pure dependency-source change — no register/poll/entity behavior touched.

## [0.3.3] - 2026-08-14

### Fixed

- `total_increasing` energy sensors (solar/load/import/export generation and battery in/out)
  flapped `unavailable` repeatedly whenever their underlying component failed a poll, even
  when the failure was brief and the component had a perfectly good last-known value.
  Confirmed live overnight (2026-08-13 → 08-14, ~21:12–06:05 local): the same physical
  RS485/bus trouble hit both this integration and `solax_modbus` reading the same inverter
  at the same time, but `solax_modbus`'s per-register quarantine held the energy counters at
  their last good value throughout, while this integration's uniform per-component
  `available` check surfaced every failed poll as `unavailable` on the four energy sensors,
  dozens of times over the night.
  - Rather than reaching for `solax_modbus`'s heavier register-bisection/quarantine engine
    (already scoped out in favor of a lighter design — see the 0.1.8 entry above), extended
    the entity-layer smoothing already shipped in 0.3.2 for value dips to the failure axis:
    a `TOTAL_INCREASING` sensor's own `available` now only tracks whether the coordinator's
    link is up at all (`SofarEntity._link_available`, a dead link still hides it), not
    whether this specific poll's component happened to fail — `native_value` was already
    returning the component's last successfully read value regardless of poll outcome, so
    this stops hiding a value the entity already has.
  - Deliberately scoped to `state_class == TOTAL_INCREASING` only: an `unavailable` grid
    voltage or instant power reading during a real fault is meaningful and should still
    surface as such. Only cumulative counters are the case where "flat because nothing
    changed overnight" and "flat because the read failed" are indistinguishable and equally
    harmless downstream.
  - Not addressed here: this integration still has no equivalent to `solax_modbus`'s
    communication-health diagnostic entity, so a genuine multi-hour link problem is no
    longer visible-but-noisy — it's just invisible. Left as a separate follow-up.

### Verification

- New regression test in `tests/lib/test_smoke.py`
  (`total-increasing-holds-available-through-failed-poll`) covers: a `TOTAL_INCREASING`
  sensor stays available and keeps its last value when its own component fails a poll; a
  plain measurement sensor (`grid_frequency`) still goes unavailable on its own component's
  failed poll, confirming the override doesn't leak beyond `TOTAL_INCREASING`; a genuinely
  dead link (`last_update_success=False`) still overrides the hold.
- `ruff`/`mypy` clean; all four `tests/lib/` scripts pass.
- Pure entity-layer availability change — no register/poll behavior touched, so mock
  verification is sufficient; not yet re-deployed to `hatest` to watch tonight's window.

## [0.3.2] - 2026-08-13

### Fixed

- Energy counters (`total_increasing` sensors — solar/load/import/export generation and
  battery in/out, 12 fields total) occasionally logged Home Assistant's "state is not
  strictly increasing" warning, which links to "create a bug report" in the Logs UI — real
  users hitting this would file spurious issues. Confirmed the underlying dip happens on
  **both** `solax_modbus` and this integration against the same physical inverter
  (`load_consumption_total` briefly reading 17506.3 after 17506.4): the device's own
  firmware occasionally returns an inconsistent snapshot of a 32-bit counter split across
  two registers, not something either integration's Modbus client causes or can prevent by
  reading more carefully.
  - Checked HA core's `reset_detected()`/`warn_dip()` in
    `homeassistant/components/sensor/recorder.py`: a decrease only corrupts Energy
    dashboard statistics if it drops below 90% of the previous value, and the warning logs
    only once per entity ever — so the dips observed here (~0.003% and ~0.0006%) were
    already harmless to the actual statistics. The fix is about the log noise and the
    "file a bug report" prompt, not a correctness bug in the Energy dashboard.
  - `SofarSensor` now keeps a per-entity high-water mark for `total_increasing` fields and
    holds at it through a dip smaller than 1% of the mark — comfortably above the observed
    noise, comfortably below HA's own 90% reset threshold, so a genuine reset (a daily
    counter's midnight rollover, an actual device counter reset) still passes straight
    through untouched.
  - Deliberately scoped to `ha-sofar-modbus`'s entity layer, not the `sofar-modbus`
    library: "look monotonic for HA's statistics engine" is HA-integration policy, not
    something the register-decode library should encode — the library keeps faithfully
    reporting whatever the device says.

### Verification

- New regression test in `tests/lib/test_smoke.py` covers all four cases: initial value
  accepted, a small dip held, a real increase passed through, a genuine large drop (reset)
  passed through immediately.
- `ruff`/`mypy` clean; `test_smoke.py` and `test_write_entities.py` both still pass.
- Pure entity-layer smoothing change — no register/poll behavior touched, so mock
  verification is sufficient; not separately exercised against real hardware.

## [0.3.1] - 2026-08-13

### Fixed

- Enum-typed sensors (`System State`, `Update System Time Operation Result`, `Parallel
  Master-Salve`, `BatConfig: Protocol`, `BatConfig: Cell Type`) rendered as bare numbers
  (`0`, `1`, `2`...) instead of text — noticed live on `hatest`, where `System State`
  showed `2` where `solax_modbus` shows "Grid-connected". Root cause: these fields decode
  to a real `IntEnum` (`SystemState` etc. in the `sofar-modbus` library), but
  `SofarSensorDescription` never set `device_class=SensorDeviceClass.ENUM`/`options`, and
  `SofarSensor.native_value` returned the raw enum member — Python 3.11 changed
  `IntEnum.__str__` to print just the int, unlike plain `Enum`.
  - `scripts/generate_sofar_model.py` now detects enum-typed fields from the library's own
    field metadata (`NumberField.convert`, whenever it's an `IntEnum` subclass — `IntFlag`
    fields like the Fault sensors are excluded automatically, since `IntFlag` doesn't
    subclass `IntEnum`) and emits `device_class=ENUM`/`options` for them, with labels
    generated mechanically from the enum member names (Title Case) rather than depending on
    upstream `plugin_sofar.py`'s own `scale` dict, which isn't a real
    `SensorEntityDescription` field and was never extracted.
  - `SofarSensor.native_value` now translates any `IntEnum` value through the matching
    label before returning it.
  - Labels don't byte-for-byte match `solax_modbus`'s hand-picked strings (e.g. `"Grid
    Connected"` vs `"Grid-connected"`) — acceptable, nothing else in this integration
    mirrors upstream's exact text.

### Verification

- New regression test in `tests/lib/test_smoke.py` asserts `system_state`'s `native_value`
  returns `"Grid Connected"`, not a raw enum/int, against the mock backend.
- `ruff`/`mypy` clean; `test_smoke.py` and `test_write_entities.py` both still pass.
- Pure display-mapping change — no register/poll behavior touched, so mock verification is
  sufficient; not separately exercised against real hardware.

## [0.3.0] - 2026-08-13

### Added

- Phase 4: `select`/`number`/`button` write entities for the HYBRID-only settings the
  `sofar-modbus` library already had full read/write support for — **Charger Use Mode**
  (`select`, immediate write), **EPS Mode** (`select`, immediate write), and **Passive
  Mode** (`number`/`select`/`button` for the timeout pair, `number`×3/`button` for the
  grid-power/battery-power triple, both staged-then-commit like FeedIn Limitation and
  Active Power Control).
- New **Read EPS registers** setup option (`CONF_READ_EPS`, default off) — without it,
  `read_eps` was never passed to `SofarInverter`, so the `eps` component (and its existing
  sensor) was never served on any inverter, regardless of hardware. EPS Mode needed this
  fixed to mean anything.

### Verification

- **No HYBRID Sofar inverter is available to test against** (unchanged from Phase 3/4
  planning) — these entities are tested only against a synthetic HYBRID identity via
  `modbus_connection.mock`, the same tooling and depth the three Phase 2 controls had
  before real hardware existed to try them on. `tests/lib/test_write_entities.py` gained a
  second (HYBRID) fixture and 7 new tests covering every new entity's immediate-write or
  stage-then-commit behavior plus one failure-surfaces case; all pass.
- None of the new entities can appear on a PV-only inverter — component-gated the same way
  every other write entity here is — so this ships with zero effect on the live or hatest
  instances, both PV-only.
- `tests/lib/test_smoke.py` unaffected (sensor-only); still passes unchanged.

## [0.2.2] - 2026-08-13

### Fixed

- The 4 mypy errors that had been sitting as accepted baseline noise since before Phase 2
  are gone, not worked around:
  - `entity.py`: `coordinator.config_entry` is typed `ConfigEntry | None` on
    `DataUpdateCoordinator` generically (some coordinators run without one), but this one
    never does — added the `assert` the code already relies on implicitly.
  - `sensor.py`: `SofarSensor.native_value` was typed `-> object`, wider than
    `SensorEntity`'s own `str | int | float | date | Decimal | None`, which mypy treats as
    an invalid override. Narrowed to `str | int | float | date | None` (every field this
    reads decodes to one of those; `bool`/`IntEnum`/`IntFlag` are already `int` subtypes,
    `datetime` is already a `date` subtype — nothing here ever produces a `Decimal`).
  - `connection.py`: `build_connection`/`unit_id` took `dict[str, Any]`, but a config
    entry's own `.data` is a read-only `MappingProxyType` — not a `dict` structurally, even
    though both functions only ever read from it. Widened both to `Mapping[str, Any]`.

### Verification

- `ruff`/`mypy` — zero errors, not 4 accepted ones. All four `tests/lib/` scripts pass.

## [0.2.1] - 2026-08-13

### Fixed

- Dropped `entity_category = CONFIG` from all Phase 2 write entities (`select`/`number`/
  `switch`/`button`). Set on the assumption these were set-once settings; real usage on the
  test instance showed otherwise — they're the primary way to interact with the inverter, and
  `CONFIG` entities get tucked into a separate Configuration section below the main Controls
  section on the device page instead of showing at the top.

### Changed

- README: Status section updated with the first real-hardware findings for the write
  entities (test instance, same physical inverter as production). FeedIn Limitation
  Mode/Maximum Power confirmed via live write-and-readback; flagged as likely inert on
  installs without an external CT/meter feeding `active_power_pcc_total` (this one included —
  no fault bits set, but nothing to measure export with either). Active Power Control writes
  reach the device without error but a clean curtailment observation is still pending.
  Documented two things that caused real confusion in testing: the Update buttons commit
  whatever's staged-or-live *at the moment of the press*, not changes made afterward; and
  Active Power Control's percentage is of the inverter's rated power (`Pn`), unrelated to
  FeedIn Maximum Power despite sitting next to it in the entity list.

### Verification

- `ruff`/`mypy` clean (same 4 pre-existing unrelated errors as baseline). All four
  `tests/lib/` scripts pass.

## [0.2.0] - 2026-08-13

### Added

- Phase 2: `select`/`number`/`switch`/`button` write entities.
  - **Remote Switch On Off** (`select`) writes immediately — a plain single-register field.
  - **FeedIn: Limitation Mode** (`select`) + **FeedIn: Maximum Power** (`number`) +
    **FeedIn: Update** (`button`), and **Active Power Control** (`switch`) +
    **Active Power Control: Export Limit** (`number`) + **Active Power Control: Update**
    (`button`) — both pairs stage locally (`SofarDataUpdateCoordinator.pending`) and only
    write on the paired button press, since the device only accepts each pair as one
    combined block. Mirrors `homeassistant-solax-modbus`'s own `WRITE_DATA_LOCAL` +
    update-button shape for the same two register pairs.
  - `SofarEntity` gained a `component` parameter and now owns `available` directly (was
    duplicated per-platform in `sensor.py`; four platforms made that worth sharing).
  - Bumps the `sofar-modbus` dependency pin to `v0.1.3` — needed for its new
    `ActivePowerControl` component (`0x1105`/`0x1106`); see that project's own history for
    the register-level detail.

### Changed

- README: the writes status section no longer lists open questions — this session confirmed
  writes work on the reference hardware (live `solax_modbus` RTC-sync writes, and a
  community-confirmed `0x1105`/`0x1106` write on the same 4.4 KTLX-G3 model) and that
  `pv_power_total`'s scale factor was already correct, so both of the old blockers are gone.

### Verification

- `python3 tests/lib/test_write_entities.py` — staging vs. immediate write, paired-commit
  writes exactly one combined block, pending cleared after commit, a `ModbusError`/`ValueError`
  from a write surfaces as `HomeAssistantError` rather than a raw exception. Not yet run
  against real hardware — this integration isn't installed on the live instance yet.
- `test_smoke.py`/`test_coordinator.py`/`test_diagnostics.py` unaffected. `ruff`/`mypy` clean
  (same 4 pre-existing unrelated errors as baseline).

## [0.1.11] - 2026-08-13

### Changed

- Review feedback (Balloob): `generated_sensors.py` is gone — `SofarSensorDescription` and
  `SENSOR_DESCRIPTIONS` now live at the bottom of `sensor.py`, generated in place by
  `scripts/generate_sofar_model.py` from a `# GENERATOR: generated below` marker onward; the
  hand-written head (imports, `async_setup_entry`, `SofarSensor`) is read back from the
  existing file and preserved verbatim on every regeneration. Created a real circular import
  in the process (`sensor.py` needs `SofarConfigEntry` from `coordinator.py`; `coordinator.py`
  needed `SENSOR_DESCRIPTIONS` from `sensor.py` for its tier split) — resolved by moving that
  one import inside `_slow_tier_components()`, which only runs after both modules have
  already finished loading.
- Each generated row now only spells out a kwarg when it differs from HA's own dataclass
  default — checked directly against `homeassistant/components/sensor/__init__.py` and
  `homeassistant/helpers/entity.py` in the `core/` fork rather than assumed:
  `device_class`/`native_unit_of_measurement`/`state_class`/`entity_category`/`icon`/
  `suggested_display_precision` default to `None`, `entity_registry_enabled_default` defaults
  to `True`. Cuts a typical row from 9 lines to as few as 3.
- `probe.py` is gone. `__init__.py` no longer calls `SofarInverter.async_setup()` separately
  or wraps it in a manual `try/except` — `coordinator.async_config_entry_first_refresh()`
  already runs `async_update()` (which calls `async_setup()` internally on first use) and
  already maps any `ModbusError` to `ConfigEntryNotReady` on its own (confirmed by reading
  `DataUpdateCoordinator._async_config_entry_first_refresh()` directly in the `core/` fork,
  not assumed). Only one manual check remains afterward: `if not device.inverter_type`, for
  the unrecognized-serial case `sofar_modbus` doesn't raise for on its own.
  `SofarUnrecognizedError` moves into `config_flow.py`, the only place still needing it
  (to keep the `cannot_connect` vs `unrecognized_inverter` error-key distinction); its own
  probe switches from `async_setup()` to `async_update()` too, for the same reason
  `__init__.py`'s does — a one-time config-flow call, so the extra register reads cost
  nothing and double as validation the device actually answers.
- Relayed, not implemented: whether `sofar_modbus` itself should raise on an unrecognized
  serial instead of silently leaving `inverter_type` at zero — consistent with the same
  loud-failure-over-silent-partial-data philosophy that removed `ComponentGroup`'s old
  catch-and-continue behavior upstream (commits `115df8b`/`e7ba2dc`). `ha-sofar-modbus`
  still needs its own local check regardless of whether that lands.

### Verification

- `python3 tests/lib/test_smoke.py` — same 234-row/88-PV/173-HYBRID counts as before;
  import switched from the deleted `generated_sensors` to
  `custom_components.sofar_modbus.sensor`, same pattern `test_coordinator.py`/
  `test_diagnostics.py` already used. `test_coordinator.py`'s `slow-tier-skipped-on-off-cycles`
  case exercises `_slow_tier_components()`'s lazy import at runtime, not just mypy's static
  check. `ruff`/`mypy` clean (same 4 pre-existing unrelated errors as baseline).

## [0.1.10] - 2026-08-13

### Changed

- Migrated from `hass.data.setdefault(DOMAIN, {})[entry.entry_id]` to `entry.runtime_data`
  for storing the coordinator — the current HA config-entry-runtime-data idiom, matching
  `trovis-modbus-hass` (the reference implementation `modbus-connection`'s own docs point
  to). A new `SofarConfigEntry = ConfigEntry[SofarDataUpdateCoordinator]` type alias
  (`coordinator.py`) is threaded through `__init__.py`, `sensor.py`, and `diagnostics.py`
  in place of a bare `ConfigEntry`. `async_unload_entry` drops its `hass.data[DOMAIN].pop(...)`
  cleanup entirely — `runtime_data` is a plain attribute on the entry object, not a
  separate registry that needs clearing (confirmed against the reference's own
  `async_unload_entry`, which does nothing beyond `async_unload_platforms`).
- `entity.py` and `config_flow.py` untouched — neither reads the coordinator back via
  `hass.data`/`runtime_data`. The pre-existing mypy error on `entity.py`'s
  `coordinator.config_entry.title` access is unrelated to this (it's how HA core types
  `DataUpdateCoordinator.config_entry` as `Optional` regardless of which `ConfigEntry`
  subtype parameterizes it) and is unchanged by this migration.

### Verification

- `tests/lib/test_diagnostics.py`'s fakes updated to carry `runtime_data` directly
  instead of simulating a `hass.data` lookup. `ruff`/`mypy` clean (same 4 pre-existing
  unrelated errors as baseline, none new). All three `tests/lib/` scripts pass unchanged.

## [0.1.9] - 2026-08-13

### Added

- Config entry diagnostics (`diagnostics.py`): downloads the raw register map — every
  register the currently-served components hold, keyed by Modbus space and address —
  plus model, serial number, inverter type bitmask, and which components are served.
  Reads fresh per component rather than reusing `coordinator.data`, so the dump reflects
  live register state at download time; a component's own read failure during the
  download is recorded in `read_errors` rather than failing the whole download, matching
  how every other resilience piece in this project treats this specific flaky gateway.
  `SofarInverter` itself has no `async_read_raw()` since it stopped wrapping a
  `ComponentGroup` (0.1.7) — this reads each served component individually and merges
  the raw maps itself, the same per-component iteration `coordinator.py` already does.
  Follows `modbus-connection`'s own [integration
  guide](https://home-assistant-libs.github.io/modbus-connection/home-assistant/integration/#diagnostics).

### Verified against the reference implementation, not just the docs

- Prompted by wanting this repo to be usable as a reference itself: re-checked
  `modbus-connection`'s HA integration checklist end to end, plus fetched the official
  `developers.home-assistant.io` Modbus guide and cloned the reference implementation it
  points to (`trovis-modbus-hass`) for a real comparison, not just the docs' own
  illustrative examples.
  - **Corrected a prior claim**: entity value access here uses string-keyed
    `component`/`key` dispatch (`SofarSensorDescription`), not the docs' type-checked
    `value_fn` lambda example. Checking the reference implementation shows this isn't a
    deviation — its `TrovisSensorDescription` uses the identical `component: str` +
    `field: str` pattern, for the same reason (a large, effectively-generated attribute
    surface, not a small hand-written one). Nothing changed here; the earlier assessment
    was wrong.
  - The reference implementation also has no diagnostics download — this feature isn't
    "catching up," it's ahead of the one repo `modbus-connection`'s own docs hold up as
    the example.
  - Noted, not changed: the reference uses `entry.runtime_data = coordinator` (the
    current HA config-entry-runtime-data idiom) where this project still uses
    `hass.data.setdefault(DOMAIN, {})[entry.entry_id]` throughout `__init__.py`/
    `sensor.py`. Real difference, touches three files, and is a style migration
    unrelated to what prompted this release — left for its own decision later.

### Verification

- New `tests/lib/test_diagnostics.py`: the payload includes every served component's
  registers keyed by space/address; a component that fails mid-download is recorded in
  `read_errors` and excluded from `registers`, without affecting any other component's
  data in the same payload.

## [0.1.8] - 2026-08-13

### Added

- `coordinator.py` now gives a failed component one retry before accepting the failure,
  and splits polled components into a fast tier (read every cycle) and a slow tier —
  settings, energy counters, identity, derived from `generated_sensors.py`'s own
  `state_class` metadata rather than a separately hand-maintained list — read only every
  4th cycle (~60s at the default 15s scan interval). Prompted by comparing against
  `solax_modbus` in production, which never shows anything unavailable during the day:
  its Modbus client is constructed with `retries=1`, retrying every read once at the
  transport level before a failure is ever visible, plus a full per-register quarantine
  and background-recheck engine built around its own dynamic block re-planning.
  `modbus_connection` deliberately disables backend retries (commit `115df8b`, "the
  wrapper alone decides what happens next") and `sofar_modbus`'s `Component`/`ReadPlan`
  model has no dynamic re-blocking to bisect around a bad register, so this is scaled to
  what's actually reachable from `ha-sofar-modbus`: the retry (the practical equivalent
  of `solax_modbus`'s `retries=1`, one layer up) and the tiered cadence (fewer registers
  read per cycle, cutting exposure to the gateway's marginal timing), both entirely
  local — no `sofar_modbus`/`modbus_connection` change needed. Register-level
  bisection/quarantine stays out of scope; it would need an upstream API `sofar_modbus`
  doesn't have (excluding specific components/registers from a poll).
- A per-component `_consecutive_failures` counter, logged at `_LOGGER.debug` — no new
  diagnostic entity yet, kept minimal until there's a reason to surface it in the UI.

### Verification

- New `tests/lib/test_coordinator.py`: a component that fails once then recovers before
  the retry never appears in `UpdateReport.failed`; a failure that survives the retry is
  tracked and doesn't affect a different fast-tier component polled the same cycle; the
  existing disconnect-after-3-consecutive-timeouts recovery still fires correctly against
  the new retry-aware failure tracking; a slow-tier component is absent from both
  `updated`/`failed` on an off-cycle and present on its due cycle; a dead link
  (`ModbusConnectionError`) still raises `UpdateFailed` immediately, mid-retry included.

## [0.1.7] - 2026-08-13

### Fixed

- One slow or refused Modbus block (seen live: `GridOutput` at 0x484 timing out against the real
  inverter's gateway) no longer blanks every sensor. Bumped `sofar-modbus` to `v0.1.2`
  ([darkrain-nl/sofar-modbus@695499f](https://github.com/darkrain-nl/sofar-modbus/commit/695499f),
  three commits: `1ce1a3b`, `075016d`, `695499f`), which stopped pooling the poll into one
  `ComponentGroup` and reads each component independently instead — a component whose read fails
  keeps its previous values and is named in the returned `UpdateReport` (`updated: set[str]`,
  `failed: dict[str, ModbusError]`, keyed by the same attribute names `generated_sensors.py`
  already uses), while every other component still refreshes and notifies.
- `coordinator.py` now only raises `UpdateFailed` for `ModbusConnectionError` (a dead link) — a
  non-empty `UpdateReport.failed` is logged, not treated as a failed poll, and `coordinator.data`
  holds the report itself (the coordinator is now `DataUpdateCoordinator[UpdateReport]`, not
  `[None]`). The disconnect-after-repeated-timeouts recovery still applies, now triggered by a
  `ModbusTimeoutError` appearing in `report.failed` rather than a caught exception.
- `sensor.py`'s `SofarSensor` gained a per-entity `available` override: only the entities on a
  component that actually failed *this* poll go unavailable, checked against
  `coordinator.data.failed` — not the coordinator-wide default every other entity used to share.
- `SofarInverter.polled_components` no longer exists upstream (removed in the same three-commit
  series — there's no public "what will this device poll" surface anymore, only "what did the
  last poll attempt"). `sensor.py`'s entity-creation filter and `tests/lib/test_smoke.py` now
  derive the served set from `coordinator.data.updated | set(coordinator.data.failed)` instead —
  every component a poll attempts lands in exactly one of the two, so their union reconstructs the
  same set `polled_components` used to give, without needing the library to expose it separately.
  The smoke test's register-seeding also changed: with no pre-poll "what's served" answer
  available, it now seeds every component's fields unconditionally (an unpolled component's
  registers are simply never read, so over-seeding is harmless) rather than filtering first.

## [0.1.6] - 2026-08-13

### Changed

- Replaced the vendored `sofar/` device library (extracted from `homeassistant-solax-modbus`'s
  `plugin_sofar.py`, hand-debugged through 0.1.1–0.1.5) with the
  [`sofar-modbus`](https://github.com/darkrain-nl/sofar-modbus) dependency, pinned to `v0.1.0` via
  `git+https` (not yet on PyPI). It's a fork of an independently-built library on the same
  `modbus-connection` foundation, covering the same register map but split per register-block
  instead of one monolithic component — so it doesn't have the vendored code's `max_span`/
  scale-factor bug classes ([0.1.4], [0.1.5]) by construction. `custom_components/sofar_modbus/sofar/`
  and its two dedicated guard tests (`test_no_ha_imports.py`, `test_field_scales.py`) are gone; the
  equivalent guards now live in the library's own test suite.
- `config_flow.py`/`__init__.py` moved from the vendored two-phase `async_probe()` (raising on an
  unrecognized serial) to the library's single-phase `SofarInverter(unit)` + `async_setup()` (which
  leaves `.inverter_type` at zero instead of raising). A new local `probe.py` restores the same
  `SofarUnrecognizedError` contract on top.
- `generated_sensors.py`'s `component=` values now point at the library's ~20 per-register-block
  attributes (`grid`, `pv_1_2`, `energy`, …) instead of the vendored `realtime`/`settings`/
  `battery_pack` split. `scripts/generate_sofar_model.py` derives that mapping by introspecting the
  installed library instead of generating the register/decode layer itself — the script only emits
  HA-facing `SensorEntityDescription` metadata now.
- `sensor.py`'s serving check is now `component in device.polled_components` (per-component) instead
  of the vendored per-field `*_served_keys` sets — verified to produce an identical entity count for
  both a PV-only and a synthetic HYBRID identity via the updated `tests/lib/test_smoke.py`.

### Removed

- The BTS battery-tower sensor rows (17 fields, upstream `BATTERY_SENSOR_TYPES`): the library
  deliberately excludes the tower from its regular poll (`polled_components`) since its packs share
  one register block and are read one at a time via `async_read_pack()`, not as part of a normal
  update cycle. No live device serves these today; a battery-pack platform needs its own
  pack-selection entity as a follow-up, not a plain sensor.

### Note

- Supersedes the `writable=True` groundwork from the previous unreleased entry: the library already
  ships `parallel_address`/`remote_switch_on_off`/`charger_use_mode` as `writable=True`, plus
  `async_write_*` convenience methods for feed-in limit, EPS control, passive-mode setpoints, RTC,
  and an IV-curve-scan trigger. A number/select write platform is a smaller follow-up than
  originally scoped, still not built in this release.

## [0.1.5] - 2026-08-12

### Fixed

- `Component.max_span` capped at 48 registers on all three generated components, matching
  upstream `plugin_sofar.py`'s `block_size=48`. The library's 125-register default produced
  46- and 57-register block reads that timed out consistently against the real inverter's
  Modbus TCP gateway.

## [0.1.4] - 2026-08-12

### Fixed

- 32-bit register fields (`solar_generation_today`/`_total`, `load_consumption_*`,
  `import_energy_*`, `export_energy_*`, `battery_input_energy_*`, `battery_output_energy_*`)
  were silently losing their scale factor — the generator returned `uint32`/`int32` before the
  scale check ever ran, so these decoded as raw register counts (10x-100x too large). Caught by
  comparing live values against `homeassistant-solax-modbus` on the same inverter.

## [0.1.3] - 2026-08-12

### Fixed

- Entity filtering was checking `description.key not in component.declared_fields`, but
  `Component.restrict_fields()` deliberately leaves `declared_fields` describing the full
  static layout regardless of what was excluded (an excluded field just decodes to `None`).
  This meant nearly every generated sensor got an entity regardless of inverter type — 236 of
  254 possible sensors on a PV-only inverter instead of the correct 89. `SofarInverter` now
  records the actual per-inverter-type served-field sets separately for entity platforms to
  filter against.

## [0.1.2] - 2026-08-12

### Fixed

- Every sensor entity failed to set up with `AttributeError` on `entity_registry_visible_default`
  and `suggested_unit_of_measurement`. `SofarSensorDescription` was a bespoke dataclass
  duck-typing a few `SensorEntityDescription` fields; `SensorEntity` reads several other
  attributes straight off `entity_description` with no `_attr_` fallback. Now a real
  `SensorEntityDescription` subclass.

## [0.1.1] - 2026-08-12

### Fixed

- Reverted an in-flight switch to the `pymodbus` backend back to `tmodbus` (the intended
  backend). The switch had been a workaround for `homeassistant-solax-modbus`'s exact
  `tmodbus==0.4.1` pin conflicting with this integration's `tmodbus>=0.5.1` requirement, but
  that pin gets re-applied on every Home Assistant restart while `solax_modbus` is loaded, so
  changing backends only masked the conflict. Documented instead: don't run this alongside
  `solax_modbus` on the same Home Assistant instance.

## [0.1.0] - 2026-08-12

### Added

- Initial release. Read-only sensor platform (TCP only) for Sofar inverters, built on
  [`modbus-connection`](https://github.com/home-assistant-libs/modbus-connection) instead of a
  hand-rolled Modbus hub. Register map and entity descriptions generated from upstream
  `homeassistant-solax-modbus`'s `plugin_sofar.py` via `scripts/generate_sofar_model.py`.
  Config flow probes the inverter's serial number to classify the model and determine which
  registers it serves.
