# Intergas Comfort Touch (new gateway) for Home Assistant

A Home Assistant custom integration for Intergas boilers connected through the
**current Intergas gateway**: the one supplied with the Intergas Comfort Touch
smart thermostat (for example "Comfort Touch slimme thermostaat incl.
Gateway") and controlled with the Intergas Comfort Touch app.

This gateway has no local interface: it only talks to the Intergas cloud. The
integration therefore uses the same cloud API as the Comfort Touch app, and
needs your Comfort Touch account.

Have the **older LAN2RF gateway** instead? Use Home Assistant's built-in
[`incomfort`](https://www.home-assistant.io/integrations/incomfort/)
integration, which talks to that gateway directly on your network.

### Which gateway do I have?

- **Easiest:** add this integration. It checks your Comfort Touch account
  first, and if the account belongs to the older platform it stops and tells
  you to use `incomfort`.
- **On your network:** open `http://<gateway IP address>/` in a browser. The
  older LAN2RF gateway shows status pages there (that is what `incomfort`
  reads); the current gateway shows nothing.

Both gateways can be connected with a network cable, so the connection type
does not tell them apart. Internally the API calls the new platform "Orion"
(`ARCH_2_0`) and the old one `ARCH_1_0`; you will only see those names in
diagnostics.

> **Unofficial.** This project is not affiliated with, endorsed by or supported
> by Intergas. The API is undocumented and may change without notice.

## Status

Early release (0.x). Developed against an Intergas Xtreme boiler with a
Comfort Touch thermostat, single zone.

| Feature | State |
|---|---|
| Room temperature, target temperature | ✅ |
| Set target temperature | ✅ tested |
| HVAC mode / action (read) | ✅ |
| Switch between manual (`heat`) and week schedule (`auto`) | ✅ |
| Turn the boiler off / on (`off` mode) | ⚠️ implemented (same request as the app), not yet confirmed on a real boiler |
| Maximum supply temperature (P050) | ✅ |
| Presets (select from the climate card, edit their temperatures) | ✅ |
| Next schedule change (climate attributes) | ✅ |
| Adaptive schedule switch | ✅ |
| Gas consumption (heating / hot water / total), burner hours | ✅ |
| Heat pump electricity use and heat output (total and yesterday), COP, hours | ✅ (hybrid HCU Xtend) |
| Water pressure, outdoor temperature, burner, online, operating mode | ✅ |
| Redacted diagnostics download | ✅ |
| Editing the week schedule's time slots | ❌ not yet (use the app) |
| Holiday mode | ❌ not yet (use the app) |

## Requirements

- An Intergas Comfort Touch account on the new platform. The integration
  checks this when you add it and refuses accounts on the legacy platform.
- Internet access from Home Assistant to `app-api.orion.intergas.nl`.

## Installation

### HACS (custom repository)

1. HACS → ⋮ → **Custom repositories** → add
   `https://github.com/ldanicel/ha-intergas-orion` as an **Integration**.
2. Install **Intergas (Orion / Comfort Touch)** and restart Home Assistant.

### Manual

1. Copy `custom_components/intergas_orion/` into your Home Assistant
   `config/custom_components/` directory.
2. Restart Home Assistant.

### Configuration

Settings → Devices & Services → **Add Integration** →
"Intergas (Orion / Comfort Touch)", then sign in with your Comfort Touch email
and password.

## Entities

- **Climate**, one per zone: current room temperature, target temperature,
  HVAC mode and action (heating / idle / off).
  - `heat` runs on the manual setpoint, `auto` follows the week schedule
    (holiday schedules also show as `auto`). Switching between them turns the
    app's week schedule off or on.
  - `off` turns the boiler off, the same as the app's device setting
    "operational state". Choosing `heat` or `auto` (or `climate.turn_on`)
    turns it back on. Only offered when the boiler reports that setting.
  - Presets are the ones defined in the app. Selecting one sets the zone to
    the preset's temperature. In `auto` mode the active preset is the one the
    schedule is currently on.
  - Attributes: `schedule_enabled`, `adaptive_schedule_enabled`,
    `next_schedule_change`, `next_schedule_preset`, `next_schedule_temperature`.
- **Maximum supply temperature** (number, configuration): the boiler's
  maximum central-heating flow temperature (P050), from the boiler's own
  parameter list. Same setting as the app's device settings.
- **Sensors** (created only if the cloud reports the value):
  - Gas consumption: heating, hot water and their total, in m³ and usable in
    the Energy dashboard. The API does not state the unit; m³ matches the
    values the app shows.
  - Heating and hot water burner hours, power-on hours.
  - Heat pump (hybrid systems such as the HCU Xtend): electricity
    consumption and heat output as running totals in kWh (the electricity
    total can be added to the Energy dashboard as an individual device),
    yesterday's electricity consumption, heat output and COP, and heat pump
    hours. The cloud reports the running totals in whole kWh and no live
    power (kW). For today's consumption so far, add a **Utility meter**
    helper with a daily cycle on the electricity consumption sensor.
  - Water pressure, central heating temperature, outdoor temperature (the
    cloud's weather for your location).
  - Operating mode (off / manual / week schedule / holiday / error), lockout code,
    last seen, heater/gateway signal strength (disabled by default).
- **Binary sensors**: burner (on while heating) and online (the gateway
  reported to the cloud in the last 10 minutes).
- **Switch**: adaptive schedule (the thermostat starts heating early so the
  room reaches the scheduled temperature on time).
- **Numbers**: the temperature of each preset.

## How it works

- Signs in with `POST /v1/accounts/authenticate` and uses the returned JWT as a
  bearer token. When the token expires (about an hour) it signs in again.
- Polls the heaters and zones every 30 seconds by default; change it under
  Settings → Devices & services → Intergas → Configure (15–300 seconds).
  Statistics, weather, presets and the week schedule are read every 5 minutes,
  and right after a change made from Home Assistant.
- Setting a temperature sends `PATCH .../zones/{n}` with
  `{"temperature_heater_setpoint": <value>}`, as the app does. The cloud
  accepts it immediately (HTTP 202) and the thermostat reports the new value
  about 40-70 seconds later; until then the entity shows the requested value.

See [docs/API.md](docs/API.md) for what is known about the API.

## Privacy

Your email and password are stored in Home Assistant's config entry, like any
other integration that signs in to a cloud account, and are only sent to
`app-api.orion.intergas.nl` over HTTPS. Nothing else is collected or sent
anywhere.

Debug logging (`custom_components.intergas_orion: debug`) records every API
request and response, with account, heater, gateway and preset ids, serial
numbers and names replaced by placeholders. The login request is never
logged.

## Removal

Settings → Devices & services → Intergas → ⋮ → **Delete**, then remove
`custom_components/intergas_orion` (or uninstall it in HACS) and restart Home
Assistant.

## Development

```bash
pip install -r requirements_test.txt
pytest
```

The tests run the integration inside Home Assistant against a mocked cloud API.

## Contributing

Issues and pull requests are welcome, especially from multi-zone setups and
other boiler models. When reporting a problem, attach the diagnostics file
(Settings → Devices & services → Intergas → ⋮ → **Download diagnostics**):
ids, serial numbers, names and credentials are already removed from it.
Debug logs are redacted the same way.

## Acknowledgements

Developed with the assistance of Claude (Anthropic).

## License

[MIT](LICENSE)
