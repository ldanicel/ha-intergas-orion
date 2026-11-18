"""Number entities for Intergas Orion: the temperature of each preset, and the maximum CH supply temperature."""
from __future__ import annotations

from time import monotonic

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import IntergasApiError
from .climate import preset_label
from .const import MAX_TEMP, MIN_TEMP, PARAM_MAX_SUPPLY_TEMPERATURE
from .coordinator import IntergasCoordinator
from .entity import IntergasEntity

PENDING_TIMEOUT = 180


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: IntergasCoordinator = entry.runtime_data
    entities: list[NumberEntity] = [
        IntergasPresetTemperature(coordinator, hid, preset["preset_id"])
        for hid, bundle in coordinator.data.items()
        for preset in bundle.get("presets", [])
        if preset.get("preset_id")
    ]
    entities += [
        IntergasMaxSupplyTemperature(coordinator, hid)
        for hid, bundle in coordinator.data.items()
        if PARAM_MAX_SUPPLY_TEMPERATURE in (bundle.get("parameters") or {})
    ]
    async_add_entities(entities)


class IntergasPresetTemperature(IntergasEntity, NumberEntity):
    """The temperature of one preset (used by the week schedule)."""

    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_min_value = MIN_TEMP
    _attr_native_max_value = MAX_TEMP
    _attr_native_step = 0.5
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator: IntergasCoordinator, heater_id: str, preset_id: str):
        super().__init__(coordinator, heater_id)
        self._preset_id = preset_id
        self._attr_unique_id = f"{heater_id}_preset_{preset_id}"
        self._pending: float | None = None
        self._pending_until = 0.0

    @property
    def _preset(self) -> dict:
        return next(
            (p for p in self._bundle.get("presets", []) if p.get("preset_id") == self._preset_id),
            {},
        )

    @property
    def name(self) -> str:
        return f"{preset_label(self._preset)} preset temperature"

    @property
    def available(self) -> bool:
        return super().available and bool(self._preset)

    @property
    def native_value(self) -> float | None:
        reported = self._preset.get("temperature")
        if self._pending is not None:
            if reported == self._pending or monotonic() > self._pending_until:
                self._pending = None
            else:
                return self._pending
        return reported

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self.coordinator.client.set_preset_temperature(
                self._heater_id, self._preset_id, value
            )
        except IntergasApiError as err:
            raise HomeAssistantError(f"Changing the preset failed: {err}") from err
        self._pending = round(float(value), 1)
        self._pending_until = monotonic() + PENDING_TIMEOUT
        self.coordinator.schedule_slow_refresh()
        self.async_write_ha_state()


class IntergasMaxSupplyTemperature(IntergasEntity, NumberEntity):
    """Maximum central-heating supply (flow) temperature, boiler parameter P050.

    Lower values make the boiler run longer and condense better; too low and the
    house no longer gets warm on cold days.
    """

    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_translation_key = "max_supply_temperature"
    _attr_icon = "mdi:thermometer-chevron-up"

    def __init__(self, coordinator: IntergasCoordinator, heater_id: str):
        super().__init__(coordinator, heater_id)
        self._attr_unique_id = f"{heater_id}_param_{PARAM_MAX_SUPPLY_TEMPERATURE}"
        self._pending: int | None = None
        self._pending_until = 0.0

    @property
    def _param(self) -> dict:
        return (self._bundle.get("parameters") or {}).get(PARAM_MAX_SUPPLY_TEMPERATURE) or {}

    @property
    def available(self) -> bool:
        return super().available and bool(self._param)

    @property
    def native_min_value(self) -> float:
        return float((self._param.get("range") or {}).get("min_value", 10))

    @property
    def native_max_value(self) -> float:
        return float((self._param.get("range") or {}).get("max_value", 90))

    @property
    def native_value(self) -> float | None:
        reported = self._param.get("value")
        if self._pending is not None:
            if reported == self._pending or monotonic() > self._pending_until:
                self._pending = None
            else:
                return self._pending
        return reported

    @property
    def extra_state_attributes(self) -> dict:
        p = self._param
        return {"display_code": p.get("display_code"), "factory_value": p.get("nom_value"),
                "status": p.get("status"), "pending": self._pending is not None}

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self.coordinator.client.set_parameter(self._heater_id, PARAM_MAX_SUPPLY_TEMPERATURE, int(round(value)))
        except IntergasApiError as err:
            raise HomeAssistantError(f"Changing the maximum supply temperature failed: {err}") from err
        self._pending = int(round(value))
        self._pending_until = monotonic() + PENDING_TIMEOUT
        self.coordinator.schedule_slow_refresh()
        self.async_write_ha_state()
