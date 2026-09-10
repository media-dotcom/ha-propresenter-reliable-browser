"""Services for ProPresenter integration."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    HomeAssistantError,
    ServiceValidationError,
    Unauthorized,
    UnknownUser,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .api import (
    ProPresenterConnectionError,
    ProPresenterNotFoundError,
    ProPresenterRequestError,
)
from .const import (
    DOMAIN,
    SERVICE_REFRESH_CACHE,
    SERVICE_SHOW_MESSAGE,
    SERVICE_TRIGGER_SLIDE,
)
from .coordinator import ProPresenterCoordinator
from .presentation import find_slide

_LOGGER = logging.getLogger(__name__)

# Service schema for show_message
SHOW_MESSAGE_SCHEMA = vol.Schema(
    {
        vol.Required("message"): cv.string,
        vol.Optional("tokens", default={}): vol.Schema({cv.string: cv.string}),
    }
)

TRIGGER_SLIDE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_ids,
        vol.Required("slide_index"): vol.All(vol.Coerce(int), vol.Range(min=0)),
        vol.Optional("expected_presentation_uuid"): cv.string,
        vol.Optional("expected_metadata_revision"): cv.string,
    }
)


def _resolve_trigger_coordinator(hass: HomeAssistant, entity_id: str):
    """Resolve exactly one active-presentation sensor to its stream coordinator."""
    registry_entry = er.async_get(hass).async_get(entity_id)
    if (
        registry_entry is None
        or registry_entry.platform != DOMAIN
        or registry_entry.domain != "sensor"
        or not registry_entry.config_entry_id
        or not registry_entry.unique_id.endswith("_active_presentation")
    ):
        raise ServiceValidationError(
            f"{entity_id} is not a ProPresenter active-presentation sensor"
        )
    config_entry = hass.config_entries.async_get_entry(registry_entry.config_entry_id)
    if not config_entry or not config_entry.runtime_data:
        raise HomeAssistantError(f"ProPresenter entry for {entity_id} is not loaded")
    coordinator = config_entry.runtime_data.get("streaming_coordinator")
    if coordinator is None:
        raise HomeAssistantError("ProPresenter streaming coordinator is unavailable")
    return coordinator


async def _check_control_permission(
    hass: HomeAssistant, call: ServiceCall, entity_id: str
) -> None:
    """Require the authenticated caller's control policy for live triggers."""
    user_id = call.context.user_id
    if not user_id:
        return
    user = await hass.auth.async_get_user(user_id)
    if user is None:
        raise UnknownUser(user_id=user_id)
    if not user.permissions.check_entity(entity_id, POLICY_CONTROL):
        raise Unauthorized(
            user_id=user_id,
            entity_id=entity_id,
            permission=POLICY_CONTROL,
        )


def async_setup_services(hass: HomeAssistant) -> None:
    """Set up services for ProPresenter integration."""

    async def async_show_message(call: ServiceCall) -> None:
        """Handle the show_message service call."""
        message_identifier = call.data.get("message")
        tokens = call.data.get("tokens", {})

        if not message_identifier:
            _LOGGER.error("Message UUID or name is required")
            return

        _LOGGER.debug(
            "Show message service called: message=%s, tokens=%s",
            message_identifier,
            tokens,
        )

        # Find all ProPresenter integrations
        found = False
        for entry_id in hass.data.get(DOMAIN, {}):
            coordinator: ProPresenterCoordinator = hass.data[DOMAIN][entry_id]

            # Find the message by UUID or name
            messages = coordinator.data.get("messages", [])
            for message in messages:
                message_data = message.get("id", {})
                message_uuid = message_data.get("uuid")
                message_name = message_data.get("name")

                # Check if identifier matches UUID or name
                if (
                    message_uuid == message_identifier
                    or message_name == message_identifier
                ):
                    # Build token structure matching ProPresenter's format
                    # Need to match token names with their UUIDs from the message definition
                    token_data = {}
                    if tokens:
                        message_tokens = message.get("tokens", [])
                        for token_name, token_value in tokens.items():
                            # Find the token UUID
                            for msg_token in message_tokens:
                                if msg_token.get("name") == token_name:
                                    token_data[token_name] = token_value
                                    break

                    # Show the message with tokens if provided
                    await coordinator.api.show_message(
                        message_uuid, token_data if token_data else None
                    )
                    await coordinator.async_request_refresh()

                    _LOGGER.info(
                        "Showed message: %s (UUID: %s) with tokens: %s",
                        message_name,
                        message_uuid,
                        token_data,
                    )
                    found = True
                    break

            if found:
                break

        if not found:
            _LOGGER.error("Message not found: %s", message_identifier)

    # Register the service
    hass.services.async_register(
        DOMAIN,
        SERVICE_SHOW_MESSAGE,
        async_show_message,
        schema=SHOW_MESSAGE_SCHEMA,
    )

    async def async_refresh_presentation_cache(call: ServiceCall) -> None:
        """Force refresh presentation and playlist caches."""
        _LOGGER.info("Refreshing presentation and playlist caches via service call")

        for entry_id in hass.data.get(DOMAIN, {}):
            coordinator: ProPresenterCoordinator = hass.data[DOMAIN][entry_id]

            # Invalidate playlist cache in coordinator
            coordinator.invalidate_playlist_cache()

            # Request coordinator refresh to fetch new data
            await coordinator.async_request_refresh()
            if coordinator.streaming_coordinator:
                try:
                    await coordinator.streaming_coordinator.async_refresh_active_presentation()
                except Exception as err:
                    _LOGGER.warning(
                        "Could not refresh active presentation metadata: %s", err
                    )

        _LOGGER.info(
            "Cache refresh completed - playlist and active presentation caches refreshed"
        )
        _LOGGER.info(
            "Note: the active presentation sensor now exposes a new metadata revision"
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_REFRESH_CACHE,
        async_refresh_presentation_cache,
    )

    async def async_trigger_slide(call: ServiceCall) -> None:
        """Trigger one guarded slide in the active presentation."""
        entity_ids = call.data.get(ATTR_ENTITY_ID, [])
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]
        if len(entity_ids) != 1:
            raise ServiceValidationError(
                "propresenter.trigger_slide requires exactly one active-presentation sensor"
            )

        entity_id = entity_ids[0]
        await _check_control_permission(hass, call, entity_id)
        coordinator = _resolve_trigger_coordinator(hass, entity_id)
        expected_uuid = call.data.get("expected_presentation_uuid")
        expected_revision = call.data.get("expected_metadata_revision")
        if bool(expected_uuid) != bool(expected_revision):
            raise ServiceValidationError(
                "expected_presentation_uuid and expected_metadata_revision must be provided together"
            )

        metadata = await coordinator.async_ensure_active_presentation_details()
        snapshot = coordinator.get_active_snapshot()
        if not metadata or not coordinator.metadata_available:
            raise ServiceValidationError("Active presentation metadata is unavailable")
        if expected_uuid and (
            expected_uuid != snapshot["presentation_uuid"]
            or expected_revision != snapshot["metadata_revision"]
        ):
            raise ServiceValidationError(
                "The presentation changed while this slide was being selected; refresh and try again"
            )

        slide_index = call.data["slide_index"]
        if (
            slide_index >= snapshot["slide_count"]
            or find_slide(metadata, slide_index) is None
        ):
            raise ServiceValidationError(
                f"Slide index {slide_index} is outside the active presentation"
            )

        try:
            # This endpoint operates on the active presentation and does not
            # focus a library/presentation in the ProPresenter UI.
            await coordinator.api.trigger_active_presentation_slide(slide_index)
        except ProPresenterNotFoundError as err:
            raise HomeAssistantError(
                f"ProPresenter rejected the slide trigger: {err}"
            ) from err
        except ProPresenterRequestError as err:
            raise HomeAssistantError(f"ProPresenter trigger failed: {err}") from err
        except ProPresenterConnectionError as err:
            # Deliberately do not retry: a timeout may have reached ProPresenter.
            raise HomeAssistantError(
                f"ProPresenter trigger status is unknown; reconcile before retrying: {err}"
            ) from err

    hass.services.async_register(
        DOMAIN,
        SERVICE_TRIGGER_SLIDE,
        async_trigger_slide,
        schema=TRIGGER_SLIDE_SCHEMA,
    )


def async_unload_services(hass: HomeAssistant) -> None:
    """Unload ProPresenter services."""
    hass.services.async_remove(DOMAIN, SERVICE_SHOW_MESSAGE)
    hass.services.async_remove(DOMAIN, SERVICE_REFRESH_CACHE)
    hass.services.async_remove(DOMAIN, SERVICE_TRIGGER_SLIDE)
