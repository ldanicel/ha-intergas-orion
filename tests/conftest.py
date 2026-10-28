"""Shared fixtures for the Intergas Orion tests."""
from aioresponses import aioresponses
import pytest

API = "https://app-api.orion.intergas.nl"
AID = "acc-1"
HID = "heater-1"

HEATER = {
    "heater_id": HID,
    "name": "Boiler",
    "state": {"mode": "RUNNING_MANUAL_TEMPERATURE", "is_heating": True, "current_lockout_code": 0},
    "hardware": {"type": "Xtreme", "serial_number": "SN-TEST", "firmware_version": "2.00"},
    "number_of_zones": 1,
    "radio_frequency_performance": {"gateway_rssi": -60, "heater_rssi": -55},
}
ZONE = {"zone_number": 1, "name": "Zone A", "temperature_room": 19.8, "temperature_heater_setpoint": 20.0}
STATISTICS = {"ch_gas_consumption": 2.5, "dhw_gas_consumption": 80.25, "ch_gas_operational_hours": 2,
              "dhw_gas_operational_hours": 35, "device_poweron_hours": 2584, "ch_water_pressure": 1.57}
# Reported by a hybrid heat pump (HCU Xtend); the gas fields are null there.
HEAT_PUMP_STATISTICS = {
    "heater_id": HID, "heating_water_pressure": 1.77, "heating_hours": None, "hot_water_hours": None,
    "heating_gas_consumption": None, "hot_water_gas_consumption": None, "ch_water_pressure": 1.77,
    "device_poweron_hours": 1368, "ch_gas_operational_hours": None, "ch_electricity_consumption": 63,
    "ch_electric_operational_hours": 175, "ch_thermal_energy": 532, "dhw_gas_operational_hours": 4,
    "ch_electric_energy_consumption_yesterday": 6.8, "ch_thermal_energy_yesterday": 52.8, "ch_cop_yesterday": 7.7,
}
WEATHER = {"conditions": "BROKEN_CLOUDS", "nighttime": False, "temperature": 16.9}
PRESETS = [
    {"name": "DEFAULT_STANDBY", "temperature": 17.5, "color": "COLOR_STANDBY", "type": "STANDBY", "preset_id": "p-away"},
    {"name": "Comfort", "temperature": 21.5, "color": "COLOR_YELLOW", "type": "COMFORT", "preset_id": "p-comfort"},
    {"name": "Home", "temperature": 20.0, "color": "COLOR_WHITE", "type": "CUSTOM", "preset_id": "p-home"},
]
PARAMETERS = [
    {"title": "heater_operational_state", "value": 1, "range": {"min_value": 0, "max_value": 1},
     "options": [{"value": 0, "description": "off"}, {"value": 1, "description": "on"}], "display_code": "",
     "description": "Device operational state: ", "nom_value": 0, "unit_or_type": "enum", "status": "NORMAL", "error_description": ""},
    {"title": "ch_temperature", "value": 80, "range": {"min_value": 10, "max_value": 90}, "options": [],
     "display_code": "P050", "description": "Maximum CH temperature setpoint : ", "nom_value": 80,
     "unit_or_type": "\u00b0C", "status": "NORMAL", "error_description": ""},
]
DAY = [{"time": "07:00", "linked_preset_id": "p-home"}, {"time": "22:30", "linked_preset_id": "p-away"}]
SCHEME = {
    "schedule_enabled": False,
    "adaptive_schedule_enabled": True,
    "timetable": {d: DAY for d in ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")},
    "adaptive_time": 15,
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def api():
    with aioresponses() as mock:
        yield mock


def mock_cloud(mock, *, jwt="jwt-1", repeat=True, heater=None, extras=True, parameters=None, statistics=None):
    mock.post(f"{API}/v1/accounts/platform_check", payload={"platform": "ARCH_2_0"}, repeat=repeat)
    mock.post(f"{API}/v1/accounts/authenticate", payload={"jwt": jwt, "account_id": AID}, repeat=repeat)
    base = f"{API}/v1/accounts/{AID}/heaters/{HID}"
    mock.get(f"{API}/v1/accounts/{AID}/heaters", payload=[heater or HEATER], repeat=repeat)
    mock.get(f"{base}/zones", payload=[ZONE], repeat=repeat)
    if extras:
        mock.get(f"{base}/statistics", payload=STATISTICS if statistics is None else statistics, repeat=repeat)
        mock.get(f"{base}/weather", payload=WEATHER, repeat=repeat)
        mock.get(f"{base}/presets", payload=PRESETS, repeat=repeat)
        mock.get(f"{base}/zones/1/scheme/week", payload=SCHEME, repeat=repeat)
        mock.get(f"{base}/parameters", payload=PARAMETERS if parameters is None else parameters, repeat=repeat)
    else:
        for path in ("statistics", "weather", "presets", "zones/1/scheme/week", "parameters"):
            mock.get(f"{base}/{path}", status=404, repeat=repeat)
