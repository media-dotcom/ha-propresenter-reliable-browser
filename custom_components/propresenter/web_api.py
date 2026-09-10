"""Authenticated Home Assistant APIs for the presentation browser."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from aiohttp import web
import voluptuous as vol

from homeassistant.components.http import HomeAssistantView
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.auth.permissions.const import POLICY_READ

from .api import ProPresenterConnectionError
from .const import DOMAIN
from .coordinator import ProPresenterStreamingCoordinator
from .presentation import find_slide

_LOGGER = logging.getLogger(__name__)

WS_TYPE = "propresenter/get_active_presentation"


@dataclass
class _ResolvedTarget:
    """The integration entry and stream coordinator behind one entity."""

    coordinator: ProPresenterStreamingCoordinator
    entity_entry: er.RegistryEntry


def _resolve_target(hass: HomeAssistant, entity_id: str) -> _ResolvedTarget:
    """Resolve and validate an active-presentation sensor entity."""
    registry_entry = er.async_get(hass).async_get(entity_id)
    if (
        registry_entry is None
        or registry_entry.platform != DOMAIN
        or registry_entry.domain != "sensor"
        or not registry_entry.config_entry_id
        or not registry_entry.unique_id.endswith("_active_presentation")
    ):
        raise LookupError(
            f"Unknown ProPresenter active-presentation entity: {entity_id}"
        )

    config_entry = hass.config_entries.async_get_entry(registry_entry.config_entry_id)
    if not config_entry or not config_entry.runtime_data:
        raise LookupError(f"ProPresenter config entry is not loaded: {entity_id}")
    coordinator = config_entry.runtime_data.get("streaming_coordinator")
    if not isinstance(coordinator, ProPresenterStreamingCoordinator):
        raise LookupError(
            f"ProPresenter streaming coordinator is unavailable: {entity_id}"
        )
    return _ResolvedTarget(coordinator, registry_entry)


def _has_permission(user: Any, entity_id: str, policy: str) -> bool:
    """Check an entity policy for the authenticated Home Assistant user."""
    if user is None or not getattr(user, "permissions", None):
        return False
    try:
        return bool(user.permissions.check_entity(entity_id, policy))
    except (AttributeError, KeyError, TypeError):
        return False


def _send_ws_error(connection: Any, msg_id: int, code: str, message: str) -> None:
    """Send a consistent custom-command error."""
    connection.send_error(msg_id, code, message)


@websocket_api.websocket_command(
    {
        vol.Required("type"): WS_TYPE,
        vol.Required("entity_id"): cv.entity_id,
        vol.Optional("refresh", default=False): cv.boolean,
    }
)
@websocket_api.async_response
async def websocket_get_active_presentation(
    hass: HomeAssistant, connection: Any, msg: dict[str, Any]
) -> None:
    """Return on-demand normalized metadata for one configured entry."""
    try:
        target = _resolve_target(hass, msg["entity_id"])
    except LookupError as err:
        _send_ws_error(connection, msg["id"], "not_found", str(err))
        return

    if not _has_permission(connection.user, msg["entity_id"], POLICY_READ):
        _send_ws_error(
            connection,
            msg["id"],
            "unauthorized",
            "Read permission is required for the ProPresenter presentation entity",
        )
        return

    try:
        if msg["refresh"]:
            await target.coordinator.async_refresh_active_presentation()
        else:
            await target.coordinator.async_ensure_active_presentation_details()
    except ProPresenterConnectionError as err:
        _send_ws_error(connection, msg["id"], "metadata_unavailable", str(err))
        return
    except Exception as err:  # pragma: no cover - defensive HA boundary
        _LOGGER.exception("Failed to build ProPresenter presentation response")
        _send_ws_error(connection, msg["id"], "unknown_error", str(err))
        return

    connection.send_result(
        msg["id"], target.coordinator.build_metadata_response(msg["entity_id"])
    )


class ProPresenterThumbnailView(HomeAssistantView):
    """Serve one active presentation thumbnail through HA authentication."""

    url = "/api/propresenter/thumbnail/{entity_id}/{presentation_uuid}/{slide_index}"
    name = "api:propresenter:thumbnail"
    requires_auth = True

    async def get(self, request: web.Request, **kwargs: Any) -> web.Response:
        """Return JPEG bytes after validating entity, UUID, revision, and bounds."""
        hass: HomeAssistant = request.app["hass"]
        entity_id = request.match_info["entity_id"]
        presentation_uuid = request.match_info["presentation_uuid"]
        try:
            slide_index = int(request.match_info["slide_index"])
        except ValueError:
            return web.Response(status=400, text="slide_index must be an integer")

        revision = request.query.get("revision")
        quality_value = request.query.get("quality")
        if not revision or quality_value is None:
            return web.Response(
                status=400, text="quality and revision query parameters are required"
            )
        try:
            quality = int(quality_value)
        except ValueError:
            return web.Response(status=400, text="quality must be an integer")
        if not 200 <= quality <= 800:
            return web.Response(status=400, text="quality must be between 200 and 800")

        try:
            target = _resolve_target(hass, entity_id)
        except LookupError as err:
            return web.Response(status=404, text=str(err))

        try:
            hass_user = request["hass_user"]
        except KeyError:
            hass_user = None
        if not _has_permission(hass_user, entity_id, POLICY_READ):
            return web.Response(status=403, text="read permission is required")

        coordinator = target.coordinator
        snapshot = coordinator.get_active_snapshot()
        if (
            snapshot["presentation_uuid"] != presentation_uuid
            or snapshot["metadata_revision"] != revision
        ):
            return web.Response(status=409, text="presentation metadata is stale")
        if (
            not coordinator.metadata_available
            or slide_index < 0
            or slide_index >= snapshot["slide_count"]
            or find_slide(coordinator.metadata, slide_index) is None
        ):
            return web.Response(status=404, text="slide is not available")

        try:
            thumbnail = await coordinator.async_get_thumbnail(
                presentation_uuid, revision, slide_index, quality
            )
        except ProPresenterConnectionError as err:
            return web.Response(status=503, text=str(err))
        except Exception as err:  # pragma: no cover - defensive HTTP boundary
            _LOGGER.exception("Failed to fetch ProPresenter thumbnail")
            return web.Response(status=502, text=str(err))

        if thumbnail is None:
            current = coordinator.get_active_snapshot()
            if (
                current["presentation_uuid"] != presentation_uuid
                or current["metadata_revision"] != revision
            ):
                return web.Response(status=409, text="presentation metadata is stale")
            return web.Response(status=404, text="thumbnail was not returned")

        return web.Response(
            body=thumbnail,
            content_type="image/jpeg",
            headers={
                "Cache-Control": "private, max-age=600",
                "X-ProPresenter-Metadata-Revision": revision,
            },
        )


def async_setup_web_api(hass: HomeAssistant) -> None:
    """Register the browser's custom command and authenticated view once."""
    websocket_api.async_register_command(hass, websocket_get_active_presentation)
    hass.http.register_view(ProPresenterThumbnailView())
