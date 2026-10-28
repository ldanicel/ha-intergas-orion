# Intergas Orion API notes

What is known about the cloud API used by the Intergas Comfort Touch app for
the new Intergas gateway. It is undocumented: everything here comes from
observing the app and the API's own responses, and may change at any time.

## The new gateway

- The new Wi-Fi gateway (an ESP32-based "WLAN2RF" module) has no usable local
  API. It exposes an HTTP server on port 80 that answers every path with 404,
  including the `/protect/*.json` paths of the old LAN2RF gateway.
- It keeps one long-lived MQTT-over-TLS connection (port 8883) to the Intergas
  cloud and does all its work through it.
- So unlike the old gateway (used by Home Assistant's built-in `incomfort`
  integration), it can only be controlled through the cloud API below.

## Two platforms

The app chooses a backend per account:

```
POST /v1/accounts/platform_check   {"email": "..."}   ->  {"platform": "ARCH_1_0" | "ARCH_2_0"}
```

| | ARCH_1_0 (legacy) | ARCH_2_0 "Orion" (new) |
|---|---|---|
| Base URL | `https://api.intergasdata.nl` | `https://app-api.orion.intergas.nl` |
| Auth | session cookie + CSRF | JWT bearer |
| Paths | `/heaters/api/...` | `/v1/accounts/...` |

This integration only supports `ARCH_2_0`. The backend is FastAPI (validation
errors come back as HTTP 422 with Pydantic error bodies).

## Authentication

```
POST /v1/accounts/authenticate   {"email": "...", "password": "..."}
  -> {"jwt": "<ES256 JWT>", "account_id": "<id>", "role": "..."}
```

- The token is in the `jwt` field (not `access_token`).
- Send it as `Authorization: Bearer <jwt>` on every other call.
- It is valid for about an hour. `POST /v1/accounts/refresh` exists; the
  integration does not use it and simply signs in again on 401/403.

## Resources

`{aid}` is the `account_id` from the authenticate response, `{hid}` a heater
id, `{n}` a zone number.

```
GET   /v1/accounts/{aid}
GET   /v1/accounts/{aid}/settings
GET   /v1/accounts/{aid}/heaters
GET   /v1/accounts/{aid}/heaters/{hid}
GET   /v1/accounts/{aid}/heaters/{hid}/capabilities
GET   /v1/accounts/{aid}/heaters/{hid}/statistics
GET   /v1/accounts/{aid}/heaters/{hid}/presets
GET   /v1/accounts/{aid}/heaters/{hid}/dhw-buffer-tank
GET   /v1/accounts/{aid}/heaters/{hid}/zones
GET   /v1/accounts/{aid}/heaters/{hid}/zones/{n}
PATCH /v1/accounts/{aid}/heaters/{hid}/zones/{n}
GET   /v1/accounts/{aid}/heaters/{hid}/zones/{n}/scheme/week
GET   /v1/accounts/{aid}/gateways/{gid}/pairing-status
```

### Heater

Fields used by the integration:

| Field | Meaning |
|---|---|
| `heater_id` | heater id |
| `state.mode` | `RUNNING_MANUAL_TEMPERATURE`, `RUNNING_WEEK_SCHEME`, `RUNNING_HOLIDAY_SCHEME`, `OFF`, `ERROR`, `UNKNOWN` |
| `state.is_heating` | burner active |
| `state.current_lockout_code` | lockout / fault code |
| `hardware.type`, `hardware.serial_number`, `hardware.firmware_version` | device info |
| `number_of_zones` | zone count |
| `radio_frequency_performance.gateway_rssi`, `.heater_rssi` | RF signal strength |
| `ch_water_pressure`, `ch_temperature` | reported by some boiler models only |

### Zone

| Field | Meaning |
|---|---|
| `zone_number` | zone number, used in paths |
| `name` | zone name |
| `temperature_room` | current room temperature |
| `temperature_heater_setpoint` | current target temperature |
| `thermostat_type` | thermostat model |

### Setting the temperature

```
PATCH /v1/accounts/{aid}/heaters/{hid}/zones/{n}
{"temperature_heater_setpoint": 21.0}
```

`ZoneUpdate` has two optional fields: `name` and `temperature_heater_setpoint`.
The API answers `202 Accepted` with an empty body and applies the change
through the gateway; the zone reports the new setpoint about 40-70 seconds
later. Unknown fields (for example `target_temperature`) are also answered
with 202 and silently ignored.

### Other writes the app uses

```
PATCH  /v1/accounts/{aid}/heaters/{hid}/zones/{n}/scheme/week
       {"schedule_enabled": bool, "adaptive_schedule_enabled": bool, "timetable": {...}}
POST   /v1/accounts/{aid}/heaters/{hid}/presets               (create)
PATCH  /v1/accounts/{aid}/heaters/{hid}/presets/{preset_id}   {"name", "temperature", "color"}
DELETE /v1/accounts/{aid}/heaters/{hid}/presets/{preset_id}
PATCH  /v1/accounts/{aid}/heaters/{hid}                       {"name"}  (rename only)
GET    /v1/accounts/{aid}/heaters/{hid}/parameters            [APIParameter]
PATCH  /v1/accounts/{aid}/heaters/{hid}/parameters            {"title", "desired_value"}
```

`/parameters` lists the boiler parameters the app may change. On an Xtreme 36:
`heater_operational_state` (0 off / 1 on) and `ch_temperature` (P050, maximum
CH supply temperature, range 10-90 °C, factory 80). Each entry has `title`,
`value`, `range {min_value, max_value}`, `options`, `display_code`,
`description`, `nom_value` (factory value), `unit_or_type`, `status` and
`error_description`. The update body is one APIParameterUpdate object.

- There is no request that sets the mode directly. `state.mode` follows from
  the schedule switch: `schedule_enabled: true` runs the week scheme, `false`
  runs on the manual setpoint.
- Holiday mode in the app is two calls: switch the schedule off, then set the
  holiday temperature.
- `timetable` maps `monday` ... `sunday` to lists of
  `{"time": "HH:MM", "linked_preset_id": "<preset id>"}`.

These are taken from the app. Every field in these request bodies is
optional, so a body can carry only the field being changed (the app itself
sends `{"schedule_enabled": false}` when it turns the schedule off).

### Read-only extras

```
GET /v1/accounts/{aid}/heaters/{hid}/statistics
  {"ch_gas_consumption", "dhw_gas_consumption", "ch_gas_operational_hours",
   "dhw_gas_operational_hours", "device_poweron_hours", "ch_water_pressure", ...}
GET /v1/accounts/{aid}/heaters/{hid}/weather
  {"conditions": "BROKEN_CLOUDS", "nighttime": false, "temperature": 16.9}
```

The gas values are running totals (m³ as far as can be told; the API does not
say). `dhw-buffer-tank` answers 404 on boilers without a buffer tank.

Which statistics fields are filled in depends on the appliance. A hybrid heat
pump (`hardware.type` `HCU Xtend`) reports its gas fields as `null` and adds:

| Field | Meaning |
|---|---|
| `ch_electricity_consumption` | electricity used for heating, running total (kWh, whole numbers) |
| `ch_thermal_energy` | heat delivered, running total (kWh, whole numbers) |
| `ch_electric_energy_consumption_yesterday` | electricity used yesterday (kWh) |
| `ch_thermal_energy_yesterday` | heat delivered yesterday (kWh) |
| `ch_cop_yesterday` | yesterday's COP (heat delivered / electricity used) |
| `ch_electric_operational_hours` | heat pump running hours |
| `heating_water_pressure` | same value as `ch_water_pressure` |

It also lists `heating_hours`, `hot_water_hours`, `heating_gas_consumption`
and `hot_water_gas_consumption` (all `null` on that model). No field gives the
current electrical power (kW), and no endpoint has been found that does.

Any path ending in `/metrics` is refused by the API's proxy ("403 Forbidden,
Request forbidden by administrative rules", an HTML body) before it reaches
the API, so it is not an app endpoint.

## Help wanted

Captures from multi-zone installations, other boiler models, and the requests
the app sends when changing mode, schedule, presets or holiday mode. Please
remove email addresses, serial numbers, ids and tokens before sharing.
