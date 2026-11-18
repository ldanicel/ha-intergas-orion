"""Binary sensors for Intergas Orion."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import IntergasCoordinator
from .entity import IntergasEntity

# The gateway normally reports every few seconds; treat it as offline after this.
OFFLINE_AFTER = timedelta(minutes=10)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: IntergasCoordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = []
    for hid in coordinator.data:
        entities.append(IntergasBurner(coordinator, hid))
        entities.append(IntergasOnline(coordinator, hid))
    async_add_entities(entities)


class IntergasBurner(IntergasEntity, BinarySensorEntity):
    """On while the boiler is heating."""

    _attr_device_class = BinarySensorDeviceClass.HEAT
    _attr_translation_key = "burner"

    def __init__(self, coordinator: IntergasCoordinator, heater_id: str) -> None:
        super().__init__(coordinator, heater_id)
        self._attr_unique_id = f"{heater_id}_burner"

    @property
    def is_on(self) -> bool | None:
        return (self._heater.get("state") or {}).get("is_heating")


class IntergasOnline(IntergasEntity, BinarySensorEntity):
    """Whether the gateway has reported to the cloud recently."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "online"

    def __init__(self, coordinator: IntergasCoordinator, heater_id: str) -> None:
        super().__init__(coordinator, heater_id)
        self._attr_unique_id = f"{heater_id}_online"

    @property
    def is_on(self) -> bool | None:
        value = self._heater.get("last_seen")
        last_seen = dt_util.parse_datetime(value) if value else None
        if last_seen is None:
            return None
        return dt_util.utcnow() - last_seen < OFFLINE_AFTER
