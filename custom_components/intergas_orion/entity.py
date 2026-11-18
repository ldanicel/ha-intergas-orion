"""Shared entity base and helpers for Intergas Orion."""
from __future__ import annotations

from datetime import datetime, timedelta

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import IntergasCoordinator

WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


class IntergasEntity(CoordinatorEntity[IntergasCoordinator]):
    """An entity that belongs to one heater (boiler) device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: IntergasCoordinator, heater_id: str) -> None:
        super().__init__(coordinator)
        self._heater_id = heater_id

    @property
    def _bundle(self) -> dict:
        return self.coordinator.data.get(self._heater_id, {})

    @property
    def _heater(self) -> dict:
        return self._bundle.get("heater") or {}

    @property
    def device_info(self) -> DeviceInfo:
        hw = self._heater.get("hardware") or {}
        return DeviceInfo(
            identifiers={(DOMAIN, self._heater_id)},
            manufacturer="Intergas",
            name=self._heater.get("name", "Intergas boiler"),
            model=hw.get("type"),
            serial_number=hw.get("serial_number"),
            sw_version=hw.get("firmware_version"),
        )


def schedule_points(scheme: dict, now: datetime) -> tuple[dict | None, datetime | None, dict | None]:
    """Return (current entry, start of next entry, next entry) of a week scheme.

    Timetable entries are {"time": "HH:MM", "linked_preset_id": ...} per weekday,
    in the thermostat's local time, which is assumed to be Home Assistant's.
    """
    timetable = scheme.get("timetable") or {}
    points: list[tuple[datetime, dict]] = []
    monday = (now - timedelta(days=now.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    # Last week, this week and next week, so "current" and "next" always exist.
    for week in (-1, 0, 1):
        for day_index, day in enumerate(WEEKDAYS):
            for item in timetable.get(day) or []:
                try:
                    hour, minute = (int(x) for x in item["time"].split(":"))
                except (KeyError, ValueError, AttributeError):
                    continue
                start = monday + timedelta(
                    weeks=week, days=day_index, hours=hour, minutes=minute
                )
                points.append((start, item))
    if not points:
        return None, None, None
    points.sort(key=lambda p: p[0])
    current = None
    for start, item in points:
        if start <= now:
            current = item
        else:
            return current, start, item
    return current, None, None
