"""Stable scalar sensors for ProPresenter's dynamic presentation state."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .base import ProPresenterBaseEntity
from .coordinator import ProPresenterCoordinator, ProPresenterStreamingCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one stable active-presentation sensor per config entry."""
    static_coordinator: ProPresenterCoordinator = config_entry.runtime_data[
        "coordinator"
    ]
    streaming_coordinator: ProPresenterStreamingCoordinator = config_entry.runtime_data[
        "streaming_coordinator"
    ]
    async_add_entities(
        [
            ProPresenterActivePresentationSensor(
                streaming_coordinator, static_coordinator, config_entry
            )
        ]
    )


class ProPresenterActivePresentationSensor(ProPresenterBaseEntity, SensorEntity):
    """Expose active presentation state without expanding per slide."""

    _attr_translation_key = "active_presentation"
    _attr_icon = "mdi:presentation"

    def __init__(
        self,
        coordinator: ProPresenterStreamingCoordinator,
        static_coordinator: ProPresenterCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the stable sensor."""
        super().__init__(
            coordinator, config_entry, static_coordinator=static_coordinator
        )
        self._attr_unique_id = f"{config_entry.entry_id}_active_presentation"

    @property
    def native_value(self) -> str | None:
        """Return the active presentation name."""
        snapshot = self.coordinator.get_active_snapshot()
        return snapshot["name"] if snapshot["presentation_uuid"] else None

    @property
    def extra_state_attributes(self) -> dict[str, str | int | bool | None]:
        """Return only scalar state and metadata pointers."""
        snapshot = self.coordinator.get_active_snapshot()
        return {
            "presentation_uuid": snapshot["presentation_uuid"],
            "current_index": snapshot["current_index"],
            "current_label": snapshot["current_label"],
            "current_group": snapshot["current_group"],
            "slide_count": snapshot["slide_count"],
            "metadata_revision": snapshot["metadata_revision"],
            "slide_layer_active": snapshot["slide_layer_active"],
            "metadata_available": snapshot["metadata_available"],
        }
