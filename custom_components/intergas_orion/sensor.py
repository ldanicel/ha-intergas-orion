"""Sensors for Intergas Orion."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS,
    UnitOfEnergy,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfTime,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import IntergasCoordinator
from .entity import IntergasEntity

MODES = [
    "off",
    "running_manual_temperature",
    "running_week_scheme",
    "running_holiday_scheme",
    "error",
    "unknown",
]


def _heater(b: dict) -> dict:
    return b.get("heater", {})


def _stat(key: str) -> Callable[[dict], float | int | None]:
    return lambda b: b.get("statistics", {}).get(key)


def _total_gas(b: dict) -> float | None:
    stats = b.get("statistics", {})
    ch, dhw = stats.get("ch_gas_consumption"), stats.get("dhw_gas_consumption")
    if ch is None and dhw is None:
        return None
    return round((ch or 0) + (dhw or 0), 4)


def _pressure(b: dict) -> float | None:
    stats = b.get("statistics", {})
    for value in (
        stats.get("ch_water_pressure"),
        stats.get("heating_water_pressure"),
        _heater(b).get("ch_water_pressure"),
    ):
        if value is not None:
            return value
    return None


def _mode(b: dict) -> str | None:
    mode = _heater(b).get("state", {}).get("mode")
    if not isinstance(mode, str):
        return None
    return mode.lower() if mode.lower() in MODES else "unknown"


def _last_seen(b: dict) -> datetime | None:
    value = _heater(b).get("last_seen")
    return dt_util.parse_datetime(value) if value else None


@dataclass(frozen=True, kw_only=True)
class IntergasSensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict], float | int | str | datetime | None]


# Each reads from the heater bundle (see IntergasCoordinator). Sensors are only
# created when the value is present, since boiler models report different data.
SENSORS: tuple[IntergasSensorDescription, ...] = (
    IntergasSensorDescription(
        key="gas_total",
        translation_key="gas_total",
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        device_class=SensorDeviceClass.GAS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_total_gas,
    ),
    IntergasSensorDescription(
        key="ch_gas_consumption",
        translation_key="ch_gas_consumption",
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        device_class=SensorDeviceClass.GAS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_stat("ch_gas_consumption"),
    ),
    IntergasSensorDescription(
        key="dhw_gas_consumption",
        translation_key="dhw_gas_consumption",
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        device_class=SensorDeviceClass.GAS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_stat("dhw_gas_consumption"),
    ),
    IntergasSensorDescription(
        key="ch_gas_operational_hours",
        translation_key="ch_gas_operational_hours",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_stat("ch_gas_operational_hours"),
    ),
    IntergasSensorDescription(
        key="dhw_gas_operational_hours",
        translation_key="dhw_gas_operational_hours",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_stat("dhw_gas_operational_hours"),
    ),
    # Heat pump (for example the hybrid HCU Xtend): electricity used and heat
    # delivered for central heating, as running totals and for yesterday.
    IntergasSensorDescription(
        key="ch_electricity_consumption",
        translation_key="ch_electricity_consumption",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_stat("ch_electricity_consumption"),
    ),
    IntergasSensorDescription(
        key="ch_thermal_energy",
        translation_key="ch_thermal_energy",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_stat("ch_thermal_energy"),
    ),
    # The "yesterday" values are complete daily totals that change once a day,
    # so they get no state class (the energy device class allows no other).
    IntergasSensorDescription(
        key="ch_electric_energy_consumption_yesterday",
        translation_key="ch_electric_energy_consumption_yesterday",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=_stat("ch_electric_energy_consumption_yesterday"),
    ),
    IntergasSensorDescription(
        key="ch_thermal_energy_yesterday",
        translation_key="ch_thermal_energy_yesterday",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        value_fn=_stat("ch_thermal_energy_yesterday"),
    ),
    IntergasSensorDescription(
        key="ch_cop_yesterday",
        translation_key="ch_cop_yesterday",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=_stat("ch_cop_yesterday"),
    ),
    IntergasSensorDescription(
        key="ch_electric_operational_hours",
        translation_key="ch_electric_operational_hours",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=_stat("ch_electric_operational_hours"),
    ),
    IntergasSensorDescription(
        key="device_poweron_hours",
        translation_key="device_poweron_hours",
        native_unit_of_measurement=UnitOfTime.HOURS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_stat("device_poweron_hours"),
    ),
    IntergasSensorDescription(
        key="ch_water_pressure",
        translation_key="ch_water_pressure",
        native_unit_of_measurement=UnitOfPressure.BAR,
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_pressure,
    ),
    IntergasSensorDescription(
        key="ch_temperature",
        translation_key="ch_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda b: _heater(b).get("ch_temperature"),
    ),
    IntergasSensorDescription(
        key="outdoor_temperature",
        translation_key="outdoor_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda b: b.get("weather", {}).get("temperature"),
    ),
    IntergasSensorDescription(
        key="mode",
        translation_key="mode",
        device_class=SensorDeviceClass.ENUM,
        options=MODES,
        value_fn=_mode,
    ),
    IntergasSensorDescription(
        key="last_seen",
        translation_key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_last_seen,
    ),
    IntergasSensorDescription(
        key="heater_rssi",
        translation_key="heater_rssi",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda b: _heater(b).get("radio_frequency_performance", {}).get("heater_rssi"),
    ),
    IntergasSensorDescription(
        key="gateway_rssi",
        translation_key="gateway_rssi",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        entity_category=EntityCategory.DIAGNOSTIC,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=False,
        value_fn=lambda b: _heater(b).get("radio_frequency_performance", {}).get("gateway_rssi"),
    ),
    IntergasSensorDescription(
        key="lockout_code",
        translation_key="lockout_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda b: _heater(b).get("state", {}).get("current_lockout_code"),
    ),
)

# Created even when currently empty: a lockout code is normally null.
ALWAYS_CREATE = {"lockout_code"}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: IntergasCoordinator = entry.runtime_data
    entities = []
    for hid, bundle in coordinator.data.items():
        for desc in SENSORS:
            if desc.key in ALWAYS_CREATE or desc.value_fn(bundle) is not None:
                entities.append(IntergasSensor(coordinator, hid, desc))
    async_add_entities(entities)


class IntergasSensor(IntergasEntity, SensorEntity):
    entity_description: IntergasSensorDescription

    def __init__(self, coordinator, heater_id: str, desc: IntergasSensorDescription):
        super().__init__(coordinator, heater_id)
        self.entity_description = desc
        self._attr_unique_id = f"{heater_id}_{desc.key}"

    @property
    def native_value(self):
        return self.entity_description.value_fn(self._bundle)
