"""Climate platform for Intergas Orion — one entity per zone."""
from __future__ import annotations

from time import monotonic
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .api import IntergasApiError
from .const import (
    MAX_TEMP,
    MIN_TEMP,
    MODE_HOLIDAY,
    MODE_OFF,
    MODE_WEEK,
    PARAM_OPERATIONAL_STATE,
)
from .coordinator import IntergasCoordinator
from .entity import IntergasEntity, schedule_points

# Seconds to wait before re-reading after a write, and to keep showing the
# requested setpoint or mode if the cloud has not reported it yet. The
# thermostat usually picks up a change within 40-70 seconds.
CONFIRM_DELAY = 15
PENDING_TIMEOUT = 180


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: IntergasCoordinator = entry.runtime_data
    entities = []
    for hid, bundle in coordinator.data.items():
        for zone in bundle["zones"]:
            if "zone_number" in zone:
                entities.append(
                    IntergasZoneClimate(coordinator, hid, zone["zone_number"])
                )
    async_add_entities(entities)


def preset_label(preset: dict) -> str:
    """Display name of a preset.

    The built-in presets are named with codes the app translates, such as
    DEFAULT_STANDBY and DEFAULT_COMFORT; show those as "Standby" / "Comfort".
    """
    name = preset.get("name") or ""
    if not name or name.startswith("DEFAULT_"):
        name = name.removeprefix("DEFAULT_") or str(preset.get("type", "preset"))
        return name.replace("_", " ").title()
    return name


class IntergasZoneClimate(IntergasEntity, ClimateEntity):
    """A heating zone exposed as a climate entity."""

    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = MIN_TEMP
    _attr_max_temp = MAX_TEMP
    _attr_target_temperature_step = 0.5

    def __init__(self, coordinator: IntergasCoordinator, heater_id: str, zone_number: int):
        super().__init__(coordinator, heater_id)
        self._zone_number = zone_number
        self._attr_unique_id = f"{heater_id}_zone{zone_number}"
        # The API accepts writes with 202 and applies them asynchronously via the
        # gateway, so show the requested value until the cloud reports it.
        self._pending_target: float | None = None
        self._pending_target_until = 0.0
        self._pending_mode: HVACMode | None = None
        self._pending_mode_until = 0.0
        self._cancel_confirm = None

    # --- data accessors ---------------------------------------------------
    @property
    def _zone(self) -> dict:
        for z in self._bundle.get("zones", []):
            if z["zone_number"] == self._zone_number:
                return z
        return {}

    @property
    def _scheme(self) -> dict:
        return self._bundle.get("schemes", {}).get(self._zone_number, {})

    @property
    def _presets(self) -> list[dict]:
        return self._bundle.get("presets", [])

    def _preset_by_id(self, preset_id: str | None) -> dict | None:
        return next((p for p in self._presets if p.get("preset_id") == preset_id), None)

    def _current_schedule_preset(self) -> dict | None:
        """Preset of the week schedule step that is active right now."""
        current, _, _ = schedule_points(self._scheme, dt_util.now())
        return self._preset_by_id(current and current.get("linked_preset_id"))

    @property
    def name(self) -> str | None:
        return self._zone.get("name") or f"Zone {self._zone_number}"

    @property
    def available(self) -> bool:
        return super().available and bool(self._zone)

    @property
    def _power_param(self) -> dict:
        """The boiler's on/off parameter, if this boiler exposes it."""
        return self._bundle.get("parameters", {}).get(PARAM_OPERATIONAL_STATE, {})

    @property
    def supported_features(self) -> ClimateEntityFeature:
        features = ClimateEntityFeature.TARGET_TEMPERATURE
        if self._presets:
            features |= ClimateEntityFeature.PRESET_MODE
        if self._power_param:
            features |= ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON
        return features

    @property
    def current_temperature(self) -> float | None:
        return self._zone.get("temperature_room")

    @property
    def target_temperature(self) -> float | None:
        reported = self._zone.get("temperature_heater_setpoint")
        if self._pending_target is not None:
            if reported == self._pending_target or monotonic() > self._pending_target_until:
                self._pending_target = None
            else:
                return self._pending_target
        return reported

    @property
    def _reported_mode(self) -> HVACMode:
        mode = self._heater.get("state", {}).get("mode")
        if mode == MODE_OFF or self._power_param.get("value") == 0:
            return HVACMode.OFF
        if mode in (MODE_WEEK, MODE_HOLIDAY):
            return HVACMode.AUTO
        return HVACMode.HEAT  # RUNNING_MANUAL_TEMPERATURE

    @property
    def hvac_mode(self) -> HVACMode:
        reported = self._reported_mode
        if self._pending_mode is not None:
            if reported == self._pending_mode or monotonic() > self._pending_mode_until:
                self._pending_mode = None
            else:
                return self._pending_mode
        return reported

    @property
    def hvac_modes(self) -> list[HVACMode]:
        modes = [HVACMode.HEAT]
        if self._scheme:
            modes.append(HVACMode.AUTO)
        if self._power_param:
            modes.append(HVACMode.OFF)
        if self.hvac_mode not in modes:
            modes.append(self.hvac_mode)
        return modes

    @property
    def hvac_action(self) -> HVACAction:
        if self._reported_mode == HVACMode.OFF:
            return HVACAction.OFF
        if self._heater.get("state", {}).get("is_heating"):
            return HVACAction.HEATING
        return HVACAction.IDLE

    @property
    def preset_modes(self) -> list[str] | None:
        return [preset_label(p) for p in self._presets] or None

    @property
    def preset_mode(self) -> str | None:
        if self.hvac_mode == HVACMode.AUTO:
            preset = self._current_schedule_preset()
            # During a temporary override the setpoint differs from the preset.
            if preset and preset.get("temperature") == self.target_temperature:
                return preset_label(preset)
            return None
        for preset in self._presets:
            if preset.get("temperature") == self.target_temperature:
                return preset_label(preset)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        scheme = self._scheme
        if not scheme:
            return {}
        attrs: dict[str, Any] = {
            "schedule_enabled": scheme.get("schedule_enabled"),
            "adaptive_schedule_enabled": scheme.get("adaptive_schedule_enabled"),
        }
        current = self._current_schedule_preset()
        if current is not None:
            attrs["current_schedule_preset"] = preset_label(current)
            attrs["current_schedule_temperature"] = current.get("temperature")
        _, next_start, next_item = schedule_points(scheme, dt_util.now())
        if next_start is not None:
            preset = self._preset_by_id(next_item.get("linked_preset_id"))
            attrs["next_schedule_change"] = next_start.isoformat()
            attrs["next_schedule_preset"] = preset_label(preset) if preset else None
            attrs["next_schedule_temperature"] = preset.get("temperature") if preset else None
        return attrs

    # --- commands ---------------------------------------------------------
    def _confirm_later(self) -> None:
        """Re-read shortly after a write; a newer write replaces the pending one."""
        if self._cancel_confirm is not None:
            self._cancel_confirm()
        self._cancel_confirm = async_call_later(
            self.hass, CONFIRM_DELAY, self._confirm_refresh
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._cancel_confirm is not None:
            self._cancel_confirm()
            self._cancel_confirm = None

    async def _confirm_refresh(self, _now) -> None:
        self._cancel_confirm = None
        await self.coordinator.async_request_refresh()

    async def async_set_temperature(self, **kwargs) -> None:
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is None:
            return
        try:
            await self.coordinator.client.set_zone_temperature(
                self._heater_id, self._zone_number, temperature
            )
        except IntergasApiError as err:
            raise HomeAssistantError(f"Setting the temperature failed: {err}") from err
        self._pending_target = round(float(temperature), 1)
        self._pending_target_until = monotonic() + PENDING_TIMEOUT
        self.async_write_ha_state()
        self._confirm_later()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode not in self.hvac_modes:
            raise ServiceValidationError(f"Mode {hvac_mode} is not supported")
        client = self.coordinator.client
        try:
            if hvac_mode == HVACMode.OFF:
                # Same as the app's device setting "operational state: off".
                await client.set_parameter(self._heater_id, PARAM_OPERATIONAL_STATE, 0)
            else:
                if self._power_param.get("value") == 0:
                    await client.set_parameter(self._heater_id, PARAM_OPERATIONAL_STATE, 1)
                await client.patch_week_scheme(
                    self._heater_id,
                    self._zone_number,
                    schedule_enabled=hvac_mode == HVACMode.AUTO,
                )
                if hvac_mode == HVACMode.AUTO:
                    await self._resume_schedule_temperature()
        except IntergasApiError as err:
            raise HomeAssistantError(f"Changing the mode failed: {err}") from err
        self._pending_mode = hvac_mode
        self._pending_mode_until = monotonic() + PENDING_TIMEOUT
        self.coordinator.schedule_slow_refresh()
        self.async_write_ha_state()
        self._confirm_later()

    async def _resume_schedule_temperature(self) -> None:
        """Apply the active schedule step's temperature.

        Turning the schedule on keeps the last manual setpoint as a temporary
        override until the next schedule step, so a setpoint from heat mode
        would otherwise stay in place for hours.
        """
        preset = self._current_schedule_preset()
        temperature = preset and preset.get("temperature")
        if temperature is None or temperature == self.target_temperature:
            return
        await self.coordinator.client.set_zone_temperature(
            self._heater_id, self._zone_number, temperature
        )
        self._pending_target = round(float(temperature), 1)
        self._pending_target_until = monotonic() + PENDING_TIMEOUT

    async def async_turn_off(self) -> None:
        await self.async_set_hvac_mode(HVACMode.OFF)

    async def async_turn_on(self) -> None:
        # Back to whatever the zone's schedule switch says.
        schedule_on = self._scheme.get("schedule_enabled")
        await self.async_set_hvac_mode(HVACMode.AUTO if schedule_on else HVACMode.HEAT)

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        preset = next((p for p in self._presets if preset_label(p) == preset_mode), None)
        if preset is None or preset.get("temperature") is None:
            raise ServiceValidationError(f"Unknown preset {preset_mode}")
        # Applying a preset sets the zone to the preset's temperature.
        await self.async_set_temperature(**{ATTR_TEMPERATURE: preset["temperature"]})
