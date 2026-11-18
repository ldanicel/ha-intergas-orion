"""Switches for Intergas Orion."""
from __future__ import annotations

from time import monotonic
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import IntergasApiError
from .coordinator import IntergasCoordinator
from .entity import IntergasEntity

PENDING_TIMEOUT = 180


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: IntergasCoordinator = entry.runtime_data
    async_add_entities(
        IntergasAdaptiveSchedule(coordinator, hid, n)
        for hid, bundle in coordinator.data.items()
        for n, scheme in (bundle.get("schemes") or {}).items()
        if "adaptive_schedule_enabled" in scheme
    )


class IntergasAdaptiveSchedule(IntergasEntity, SwitchEntity):
    """Adaptive schedule: start heating early so the room is warm on time."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "adaptive_schedule"

    def __init__(self, coordinator: IntergasCoordinator, heater_id: str, zone_number: int):
        super().__init__(coordinator, heater_id)
        self._zone_number = zone_number
        self._attr_unique_id = f"{heater_id}_zone{zone_number}_adaptive_schedule"
        self._pending: bool | None = None
        self._pending_until = 0.0

    @property
    def is_on(self) -> bool | None:
        reported = (
            (self._bundle.get("schemes") or {})
            .get(self._zone_number, {})
            .get("adaptive_schedule_enabled")
        )
        if self._pending is not None:
            if reported == self._pending or monotonic() > self._pending_until:
                self._pending = None
            else:
                return self._pending
        return reported

    async def _set(self, value: bool) -> None:
        try:
            await self.coordinator.client.patch_week_scheme(
                self._heater_id, self._zone_number, adaptive_schedule_enabled=value
            )
        except IntergasApiError as err:
            raise HomeAssistantError(f"Changing the adaptive schedule failed: {err}") from err
        self._pending = value
        self._pending_until = monotonic() + PENDING_TIMEOUT
        self.coordinator.schedule_slow_refresh()
        self.async_write_ha_state()

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._set(False)
