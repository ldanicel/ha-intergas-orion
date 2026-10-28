"""Tests for the Intergas Orion integration against a mocked cloud API."""
from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from yarl import URL

from custom_components.intergas_orion.const import DOMAIN

from .conftest import AID, API, HEAT_PUMP_STATISTICS, HEATER, HID, mock_cloud

CREDS = {CONF_EMAIL: "user@example.com", CONF_PASSWORD: "secret"}


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data=CREDS, unique_id=AID)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_config_flow_success(hass: HomeAssistant, api) -> None:
    mock_cloud(api)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == AID


async def test_config_flow_wrong_platform(hass: HomeAssistant, api) -> None:
    api.post(f"{API}/v1/accounts/platform_check", payload={"platform": "ARCH_1_0"})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["errors"] == {"base": "wrong_platform"}


async def test_config_flow_invalid_auth(hass: HomeAssistant, api) -> None:
    api.post(f"{API}/v1/accounts/platform_check", payload={"platform": "ARCH_2_0"})
    api.post(f"{API}/v1/accounts/authenticate", status=401)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["errors"] == {"base": "invalid_auth"}


async def test_config_flow_cannot_connect(hass: HomeAssistant, api) -> None:
    api.post(f"{API}/v1/accounts/platform_check", status=500)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["errors"] == {"base": "cannot_connect"}


async def test_entities(hass: HomeAssistant, api) -> None:
    mock_cloud(api)
    await _setup(hass)
    climates = hass.states.async_all("climate")
    assert len(climates) == 1
    state = climates[0]
    assert state.state == "heat"
    assert state.attributes["current_temperature"] == 19.8
    assert state.attributes["temperature"] == 20.0
    assert state.attributes["hvac_action"] == "heating"
    assert state.attributes["hvac_modes"] == ["heat", "auto", "off"]
    # No request may ever go to an un-substituted account path.
    assert not any("None" in str(url) or "{aid}" in str(url) for _, url in api.requests)


async def test_set_temperature(hass: HomeAssistant, api) -> None:
    mock_cloud(api)
    zone_url = f"{API}/v1/accounts/{AID}/heaters/{HID}/zones/1"
    api.patch(zone_url, status=202, body=b"")
    await _setup(hass)
    entity_id = hass.states.async_all("climate")[0].entity_id
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": entity_id, "temperature": 21.5}, blocking=True
    )
    calls = api.requests[("PATCH", URL(zone_url))]
    assert calls[0].kwargs["json"] == {"temperature_heater_setpoint": 21.5}
    assert calls[0].kwargs["headers"]["Authorization"] == "Bearer jwt-1"
    # The cloud applies it asynchronously (202): keep showing the requested value.
    assert hass.states.get(entity_id).attributes["temperature"] == 21.5


async def test_pending_setpoint_expires(hass: HomeAssistant, api, freezer) -> None:
    """If the cloud never reports the new setpoint, fall back to the reported one."""
    from datetime import timedelta

    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    from custom_components.intergas_orion import climate

    mock_cloud(api)
    api.patch(f"{API}/v1/accounts/{AID}/heaters/{HID}/zones/1", status=202, body=b"")
    await _setup(hass)
    entity_id = hass.states.async_all("climate")[0].entity_id
    await hass.services.async_call(
        "climate", "set_temperature", {"entity_id": entity_id, "temperature": 23}, blocking=True
    )
    assert hass.states.get(entity_id).attributes["temperature"] == 23
    # freezegun swaps monotonic for a date-based clock, so use inf for "past the timeout".
    climate.monotonic = lambda: float("inf")
    try:
        freezer.tick(timedelta(seconds=climate.CONFIRM_DELAY + 1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).attributes["temperature"] == 20.0
    finally:
        from time import monotonic

        climate.monotonic = monotonic


async def test_token_expiry_reauths(hass: HomeAssistant, api) -> None:
    """A 401 on a data call triggers one fresh login and a retry."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from custom_components.intergas_orion.api import IntergasOrionClient

    api.post(f"{API}/v1/accounts/authenticate", payload={"jwt": "old", "account_id": AID})
    api.post(f"{API}/v1/accounts/authenticate", payload={"jwt": "new", "account_id": AID})
    heaters = f"{API}/v1/accounts/{AID}/heaters"
    api.get(heaters, status=401)
    api.get(heaters, payload=[{"heater_id": HID}])
    client = IntergasOrionClient(async_get_clientsession(hass), "u@example.com", "pw")
    assert await client.get_heaters() == [{"heater_id": HID}]
    calls = api.requests[("GET", URL(heaters))]
    assert [c.kwargs["headers"]["Authorization"] for c in calls] == ["Bearer old", "Bearer new"]


async def test_bad_password_starts_reauth(hass: HomeAssistant, api) -> None:
    api.post(f"{API}/v1/accounts/authenticate", status=401, repeat=True)
    entry = MockConfigEntry(domain=DOMAIN, data=CREDS, unique_id=AID)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is config_entries.ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == config_entries.SOURCE_REAUTH for f in flows)


async def test_unload(hass: HomeAssistant, api) -> None:
    mock_cloud(api)
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is config_entries.ConfigEntryState.NOT_LOADED


async def test_options_scan_interval(hass: HomeAssistant, api) -> None:
    from datetime import timedelta

    mock_cloud(api)
    entry = await _setup(hass)
    coordinator = entry.runtime_data
    assert coordinator.update_interval == timedelta(seconds=30)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"scan_interval": 20}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    coordinator = entry.runtime_data
    assert coordinator.update_interval == timedelta(seconds=20)


ZONE_URL = f"{API}/v1/accounts/{AID}/heaters/{HID}/zones/1"
SCHEME_URL = f"{ZONE_URL}/scheme/week"


def _entity(hass: HomeAssistant, domain: str, suffix: str) -> str:
    return next(s.entity_id for s in hass.states.async_all(domain) if s.entity_id.endswith(suffix))


async def test_sensors_and_binary_sensors(hass: HomeAssistant, api) -> None:
    mock_cloud(api, heater={**HEATER, "last_seen": "2000-01-01T00:00:00Z"})
    await _setup(hass)
    assert hass.states.get(_entity(hass, "sensor", "_gas_consumption")).state == "82.75"
    assert hass.states.get(_entity(hass, "sensor", "heating_gas_consumption")).state == "2.5"
    assert hass.states.get(_entity(hass, "sensor", "water_pressure")).state == "1.57"
    assert hass.states.get(_entity(hass, "sensor", "outdoor_temperature")).state == "16.9"
    assert hass.states.get(_entity(hass, "sensor", "operating_mode")).state == "running_manual_temperature"
    assert hass.states.get(_entity(hass, "binary_sensor", "burner")).state == "on"
    # last_seen long ago: the gateway counts as offline.
    assert hass.states.get(_entity(hass, "binary_sensor", "online")).state == "off"


async def test_heat_pump_energy_sensors(hass: HomeAssistant, api) -> None:
    mock_cloud(api, statistics=HEAT_PUMP_STATISTICS)
    await _setup(hass)
    expected = {
        "heat_pump_electricity_consumption": ("63", "kWh", "total_increasing"),
        "heat_pump_heat_output": ("532", "kWh", "total_increasing"),
        "heat_pump_electricity_consumption_yesterday": ("6.8", "kWh", None),
        "heat_pump_heat_output_yesterday": ("52.8", "kWh", None),
        "heat_pump_cop_yesterday": ("7.7", None, "measurement"),
        "heat_pump_hours": ("175", "h", "total_increasing"),
    }
    for suffix, (value, unit, state_class) in expected.items():
        state = hass.states.get(_entity(hass, "sensor", suffix))
        assert state.state == value, suffix
        assert state.attributes.get("unit_of_measurement") == unit, suffix
        assert state.attributes.get("state_class") == state_class, suffix
    # Null gas values on a heat pump create no gas sensors.
    assert not [s for s in hass.states.async_all("sensor") if s.entity_id.endswith("gas_consumption")]


async def test_gas_boiler_has_no_heat_pump_sensors(hass: HomeAssistant, api) -> None:
    mock_cloud(api)
    await _setup(hass)
    assert not [s for s in hass.states.async_all("sensor") if "heat_pump" in s.entity_id]


async def test_optional_endpoints_missing(hass: HomeAssistant, api) -> None:
    """Statistics, presets etc. failing must not break the integration."""
    mock_cloud(api, extras=False)
    await _setup(hass)
    state = hass.states.async_all("climate")[0]
    assert state.attributes["temperature"] == 20.0
    assert state.attributes["hvac_modes"] == ["heat"]
    assert not hass.states.async_all("number")
    assert not hass.states.async_all("switch")


async def test_set_hvac_mode_auto(hass: HomeAssistant, api) -> None:
    mock_cloud(api)
    api.patch(SCHEME_URL, status=202, body=b"")
    await _setup(hass)
    entity_id = hass.states.async_all("climate")[0].entity_id
    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": "auto"}, blocking=True
    )
    calls = api.requests[("PATCH", URL(SCHEME_URL))]
    assert calls[0].kwargs["json"] == {"schedule_enabled": True}
    assert hass.states.get(entity_id).state == "auto"


async def test_presets(hass: HomeAssistant, api) -> None:
    mock_cloud(api)
    api.patch(ZONE_URL, status=202, body=b"")
    await _setup(hass)
    state = hass.states.async_all("climate")[0]
    assert state.attributes["preset_modes"] == ["Standby", "Comfort", "Home"]
    # Manual mode at 20.0 matches the "Home" preset.
    assert state.attributes["preset_mode"] == "Home"
    assert "next_schedule_change" in state.attributes
    await hass.services.async_call(
        "climate", "set_preset_mode", {"entity_id": state.entity_id, "preset_mode": "Comfort"},
        blocking=True,
    )
    calls = api.requests[("PATCH", URL(ZONE_URL))]
    assert calls[0].kwargs["json"] == {"temperature_heater_setpoint": 21.5}


async def test_preset_temperature_number(hass: HomeAssistant, api) -> None:
    mock_cloud(api)
    url = f"{API}/v1/accounts/{AID}/heaters/{HID}/presets/p-comfort"
    api.patch(url, status=202, body=b"")
    await _setup(hass)
    entity_id = _entity(hass, "number", "comfort_preset_temperature")
    assert hass.states.get(entity_id).state == "21.5"
    await hass.services.async_call(
        "number", "set_value", {"entity_id": entity_id, "value": 22}, blocking=True
    )
    assert api.requests[("PATCH", URL(url))][0].kwargs["json"] == {"temperature": 22.0}
    assert hass.states.get(entity_id).state == "22.0"


async def test_max_supply_temperature_number(hass: HomeAssistant, api) -> None:
    mock_cloud(api)
    url = f"{API}/v1/accounts/{AID}/heaters/{HID}/parameters"
    api.patch(url, status=202, body=b"")
    await _setup(hass)
    entity_id = _entity(hass, "number", "maximum_supply_temperature")
    state = hass.states.get(entity_id)
    assert state.state == "80"
    assert state.attributes["min"] == 10 and state.attributes["max"] == 90
    assert state.attributes["display_code"] == "P050"
    await hass.services.async_call(
        "number", "set_value", {"entity_id": entity_id, "value": 60}, blocking=True
    )
    assert api.requests[("PATCH", URL(url))][0].kwargs["json"] == {"title": "ch_temperature", "desired_value": 60}
    assert hass.states.get(entity_id).state == "60"


async def test_adaptive_schedule_switch(hass: HomeAssistant, api) -> None:
    mock_cloud(api)
    api.patch(SCHEME_URL, status=202, body=b"")
    await _setup(hass)
    entity_id = _entity(hass, "switch", "adaptive_schedule")
    assert hass.states.get(entity_id).state == "on"
    await hass.services.async_call("switch", "turn_off", {"entity_id": entity_id}, blocking=True)
    calls = api.requests[("PATCH", URL(SCHEME_URL))]
    assert calls[0].kwargs["json"] == {"adaptive_schedule_enabled": False}
    assert hass.states.get(entity_id).state == "off"


def test_schedule_points() -> None:
    from datetime import datetime

    from custom_components.intergas_orion.entity import schedule_points

    from .conftest import SCHEME

    # Friday 2026-10-09 18:00: current is 07:00 "home", next is 22:30 "away".
    current, start, nxt = schedule_points(SCHEME, datetime(2026, 10, 9, 18, 0))
    assert current["linked_preset_id"] == "p-home"
    assert start == datetime(2026, 10, 9, 22, 30)
    assert nxt["linked_preset_id"] == "p-away"
    # Monday 03:00: still the Sunday 22:30 entry from the previous week.
    current, start, _ = schedule_points(SCHEME, datetime(2026, 10, 12, 3, 0))
    assert current["linked_preset_id"] == "p-away"
    assert start == datetime(2026, 10, 12, 7, 0)
    assert schedule_points({}, datetime(2026, 10, 9)) == (None, None, None)


async def test_debug_log_has_no_identifiers(hass: HomeAssistant, api, caplog) -> None:
    """Debug logs and errors must be shareable: no ids, serials, names or tokens."""
    import logging

    from homeassistant.exceptions import HomeAssistantError

    caplog.set_level(logging.DEBUG, logger="custom_components.intergas_orion")
    mock_cloud(api)
    api.patch(ZONE_URL, status=422, payload={"detail": [{"loc": ["body"], "input": {"heater_id": HID}}]})
    await _setup(hass)
    entity_id = hass.states.async_all("climate")[0].entity_id
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            "climate", "set_temperature", {"entity_id": entity_id, "temperature": 21}, blocking=True
        )
    text = "\n".join(r.getMessage() for r in caplog.records) + "\n" + str(err.value)
    assert "GET /v1/accounts/<id>/heaters/<id>/zones" in text
    for secret in (AID, HID, "SN-TEST", "jwt-1", "user@example.com", "secret", "Zone A"):
        assert secret not in text, secret


PARAMS_URL = f"{API}/v1/accounts/{AID}/heaters/{HID}/parameters"


async def test_turn_boiler_off(hass: HomeAssistant, api) -> None:
    mock_cloud(api)
    api.patch(PARAMS_URL, status=202, body=b"")
    await _setup(hass)
    entity_id = hass.states.async_all("climate")[0].entity_id
    await hass.services.async_call("climate", "turn_off", {"entity_id": entity_id}, blocking=True)
    calls = api.requests[("PATCH", URL(PARAMS_URL))]
    assert calls[0].kwargs["json"] == {"title": "heater_operational_state", "desired_value": 0}
    assert hass.states.get(entity_id).state == "off"


async def test_turn_boiler_back_on(hass: HomeAssistant, api) -> None:
    """From off, heat switches the boiler on and the schedule off."""
    from .conftest import PARAMETERS

    off = [{**p, "value": 0} if p["title"] == "heater_operational_state" else p for p in PARAMETERS]
    mock_cloud(api, parameters=off)
    api.patch(PARAMS_URL, status=202, body=b"")
    api.patch(SCHEME_URL, status=202, body=b"")
    await _setup(hass)
    entity_id = hass.states.async_all("climate")[0].entity_id
    assert hass.states.get(entity_id).state == "off"
    assert hass.states.get(entity_id).attributes["hvac_action"] == "off"
    await hass.services.async_call(
        "climate", "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": "heat"}, blocking=True
    )
    assert api.requests[("PATCH", URL(PARAMS_URL))][0].kwargs["json"] == {
        "title": "heater_operational_state", "desired_value": 1,
    }
    assert api.requests[("PATCH", URL(SCHEME_URL))][0].kwargs["json"] == {"schedule_enabled": False}
    assert hass.states.get(entity_id).state == "heat"


async def test_no_off_mode_without_parameter(hass: HomeAssistant, api) -> None:
    mock_cloud(api, parameters=[])
    await _setup(hass)
    state = hass.states.async_all("climate")[0]
    assert state.attributes["hvac_modes"] == ["heat", "auto"]


async def test_diagnostics_are_redacted(hass: HomeAssistant, api) -> None:
    import json

    from custom_components.intergas_orion.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    mock_cloud(api)
    entry = await _setup(hass)
    diag = await async_get_config_entry_diagnostics(hass, entry)
    heater = diag["heaters"]["heater_1"]
    assert heater["zones"][0]["temperature_heater_setpoint"] == 20.0
    assert heater["statistics"]["dhw_gas_consumption"] == 80.25
    text = json.dumps(diag)
    for secret in (AID, HID, "SN-TEST", "user@example.com", "secret", "Zone A", "p-home"):
        assert secret not in text, secret
