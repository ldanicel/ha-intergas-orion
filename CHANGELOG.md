# Changelog

## Unreleased

- New sensors for heat pumps (for example the hybrid HCU Xtend): electricity
  consumption and heat output (running totals in kWh, Energy dashboard
  ready), yesterday's electricity consumption, heat output and COP, and heat
  pump hours. Created only when the cloud reports them.
- `tools/orion_probe.py` also prints statistics and weather, shows error
  bodies, waits when rate limited, has an `--energy-only` flag and no longer
  stops when `platform_check` answers `ARCH_1_0` (it does so for unknown
  email addresses too).

## 0.4.0

- The climate entity has an `off` mode that turns the boiler off, the same as
  the app's device setting "operational state" (boiler parameter
  `heater_operational_state`). `heat`, `auto` and `climate.turn_on` turn it
  back on. Only offered when the boiler reports that parameter.
- Diagnostics: Settings → Devices & services → Intergas → Download
  diagnostics gives a snapshot of everything the integration read, with ids,
  serial numbers, names and credentials removed.
- Fix: a crash in the redaction of data keyed by zone number.
- Fix: every temperature or mode change registered another cleanup callback;
  only the latest pending re-check is kept now.
- Internal: per-entry data lives in `entry.runtime_data`; the maximum supply
  temperature entity name is translatable.

## 0.3.0

- New number entity **Maximum supply temperature**: the boiler's maximum
  central-heating flow temperature (parameter P050, the same setting as in the
  app's device settings). A lower value makes the boiler run
  longer and condense better. Range comes from the
  boiler (usually 10-90 °C); attributes show the display code, the factory
  value and the parameter status.

## 0.2.1

- Debug logs and error messages no longer contain account, heater, gateway or
  preset ids, serial numbers or names, so they can be shared in an issue as
  they are. Nothing is kept from a failed login response.
- `tools/orion_probe.py` also redacts every `*_id` field.

## 0.2.0

- Switch between manual (`heat`) and week schedule (`auto`) from the climate
  entity.
- Presets: select one from the climate card (sets the zone to the preset's
  temperature) and edit each preset's temperature with a number entity.
- Climate attributes for the week schedule: whether it is on, and the next
  change with its preset and temperature.
- Adaptive schedule switch.
- New sensors: gas consumption (heating, hot water, total; Energy dashboard
  ready), burner hours, power-on hours, outdoor temperature, operating mode,
  last seen. Water pressure now comes from the statistics endpoint.
- New binary sensors: burner and online.
- Statistics, weather, presets and week schedules are read every 5 minutes;
  a failure there no longer affects the climate entity.

## 0.1.2

- Fix: setting the temperature had no effect. The request now uses the
  `temperature_heater_setpoint` field, as the Comfort Touch app does; the API
  accepted and silently ignored the old `target_temperature` field.
- The requested setpoint is shown for up to 3 minutes while the thermostat
  picks it up (it usually takes 40-70 seconds).

## 0.1.1

- Setting the temperature: the cloud accepts it with HTTP 202 and applies it
  asynchronously, so the requested value is now shown until the cloud reports
  it (up to 2 minutes), with a re-check 15 seconds after the write.
- Debug logging of API requests and responses (never the login exchange).
- Errors from the API are shown in the UI when setting a temperature fails.
- Refresh interval is now configurable (Configure button, 15-300 seconds) and
  defaults to 30 seconds instead of 60, so changes made on the thermostat or in
  the app show up sooner.

## 0.1.0

First public release.

- Config flow with email/password; refuses accounts on the legacy platform.
- Re-authentication flow when the password changes.
- Climate entity per zone with current/target temperature and set temperature.
- Read-only HVAC mode and action.
- Diagnostic sensors: signal strength, lockout code, water pressure, CH temperature.
