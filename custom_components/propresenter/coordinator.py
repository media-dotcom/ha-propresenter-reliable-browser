"""DataUpdateCoordinator for ProPresenter integration."""

import asyncio
from datetime import timedelta
import logging
import secrets
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import async_get as async_get_device_registry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ProPresenterAPI, ProPresenterConnectionError
from .const import CONF_PORT, DEFAULT_PORT, DOMAIN
from .playlist import coerce_playlist_objects, normalize_playlist_catalog
from .presentation import (
    find_slide,
    find_slide_group,
    get_presentation_name,
    get_presentation_uuid,
    get_slide_index,
    normalize_presentation,
)
from .thumbnail_cache import ThumbnailCache, ThumbnailKey
from .utils import collect_playlist_uuids

_LOGGER = logging.getLogger(__name__)


class ProPresenterCoordinator(DataUpdateCoordinator):
    """ProPresenter coordinator - handles infrequently changing data via polling (firmware, name, etc)."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry) -> None:
        """Initialize coordinator."""
        self.config_entry = config_entry
        self.streaming_coordinator = None  # Set later by streaming coordinator
        self._last_known_version = (
            None  # Track version to only update device info when it changes
        )
        self._presentation_playlist_revision: str | None = None

        # Get configuration values
        host = config_entry.data[CONF_HOST]
        port = config_entry.data.get(CONF_PORT, DEFAULT_PORT)

        # Initialize API
        self.api = ProPresenterAPI(host, port)

        # Initialize DataUpdateCoordinator with longer interval for static data
        # Dynamic data will be handled by streaming
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({host}:{port})",
            update_method=self.async_update_data,
            update_interval=timedelta(
                seconds=30
            ),  # Poll static data like version every 30 seconds
        )

    async def async_update_data(self) -> dict[str, Any]:
        """Fetch static/rarely-changing data from API.

        Poll ONLY truly static data here.
        Dynamic data (looks, props, playlists state, current_look, slide_index)
        comes via streaming coordinator to avoid unnecessary API load.
        """
        try:
            # Version info - truly static (only changes on PP upgrade)
            version_info = await self.api.get_version()

            # Clear groups - rarely change (only when user adds/removes in PP)
            clear_groups = await self.api.get_clear_groups()

            # Macros - rarely change (only when user creates/deletes)
            macros = await self.api.get_macros()

            # Presentation playlist structure - cache on first fetch
            # Only re-fetch if not in cache (user can call refresh service)
            if not hasattr(self, "_cached_presentation_playlists"):
                presentation_playlists = coerce_playlist_objects(
                    await self.api.get_presentation_playlists()
                )
                # Collect all playlist UUIDs (including nested ones)
                playlist_uuids = []
                collect_playlist_uuids(presentation_playlists, playlist_uuids)

                # Fetch details for all playlists ONCE
                presentation_playlist_details_list = []
                for playlist_uuid in dict.fromkeys(playlist_uuids):
                    details = await self.api.get_presentation_playlist_details(
                        playlist_uuid
                    )
                    if details:
                        presentation_playlist_details_list.extend(
                            coerce_playlist_objects(details, details=True)
                        )

                self._cached_presentation_playlists = presentation_playlists
                self._cached_presentation_playlist_details = (
                    presentation_playlist_details_list
                )
                self._presentation_playlist_revision = secrets.token_urlsafe(24)

            # Audio playlist structure - cache on first fetch
            if not hasattr(self, "_cached_audio_playlists"):
                audio_playlists = await self.api.get_audio_playlists()
                audio_playlist_details_list = []
                if audio_playlists and isinstance(audio_playlists, list):
                    for playlist in audio_playlists:
                        playlist_id = playlist.get("id", {})
                        playlist_uuid = playlist_id.get("uuid")
                        if playlist_uuid:
                            details = await self.api.get_audio_playlist_details(
                                playlist_uuid
                            )
                            if details:
                                audio_playlist_details_list.append(details)

                self._cached_audio_playlists = audio_playlists
                self._cached_audio_playlist_details = audio_playlist_details_list

            # Media playlist structure - cache on first fetch
            if not hasattr(self, "_cached_media_playlists"):
                media_playlists = await self.api.get_media_playlists()
                media_playlist_details_list = []
                if media_playlists and isinstance(media_playlists, list):
                    for playlist in media_playlists:
                        playlist_id = playlist.get("id", {})
                        playlist_uuid = playlist_id.get("uuid")
                        if playlist_uuid:
                            try:
                                details = await self.api.get_media_playlist_details(
                                    playlist_uuid
                                )
                                if details:
                                    media_playlist_details_list.append(details)
                            except Exception as e:
                                _LOGGER.debug(
                                    "Could not fetch media playlist details for %s: %s",
                                    playlist_uuid,
                                    e,
                                )

                self._cached_media_playlists = media_playlists
                self._cached_media_playlist_details = media_playlist_details_list

            # Fetch timers
            timers = await self.api.get_timers() or []

            # Fetch video inputs
            video_inputs = await self.api.get_video_inputs() or []

            data = {
                "version": version_info,
                "clear_groups": clear_groups,
                "macros": macros,
                "timers": timers,
                "video_inputs": video_inputs,
                # Return cached playlist data
                "presentation_playlists": self._cached_presentation_playlists,
                "presentation_playlist_details_list": self._cached_presentation_playlist_details,
                "presentation_playlist_revision": self._presentation_playlist_revision,
                "audio_playlists": self._cached_audio_playlists,
                "audio_playlist_details_list": self._cached_audio_playlist_details,
                "media_playlists": self._cached_media_playlists,
                "media_playlist_details_list": self._cached_media_playlist_details,
            }
            # Cache the successful data
            self._data = data

            # Check if version has changed and update device registry if needed
            # This only calls async_update_device() if version actually changed
            await self.update_device_firmware_version()

            return data
        except ProPresenterConnectionError as err:
            raise UpdateFailed(f"Error communicating with ProPresenter: {err}") from err

    async def async_shutdown(self) -> None:
        """Close API connection on shutdown."""
        await self.api.close()

    async def update_device_firmware_version(self) -> None:
        """Update device registry with current firmware version.

        This only updates if the version has actually changed since last check.
        """
        if not self.data:
            return

        version_info = self.data.get("version", {})
        host_description = version_info.get("host_description", "")
        current_version = "Unknown"
        if host_description.startswith("ProPresenter "):
            current_version = host_description.replace("ProPresenter ", "")

        # Only update device registry if version has changed
        if current_version == self._last_known_version:
            return

        self._last_known_version = current_version

        try:
            device_registry = async_get_device_registry(self.hass)
            device = device_registry.async_get_device(
                identifiers={(DOMAIN, self.config_entry.entry_id)}
            )

            if device:
                device_registry.async_update_device(
                    device.id, sw_version=current_version
                )
        except Exception as err:
            _LOGGER.debug(f"Could not update device registry: {err}")

    def invalidate_playlist_cache(self) -> None:
        """Invalidate cached playlist data to force refresh on next poll."""
        if hasattr(self, "_cached_presentation_playlists"):
            delattr(self, "_cached_presentation_playlists")
        if hasattr(self, "_cached_presentation_playlist_details"):
            delattr(self, "_cached_presentation_playlist_details")
        self._presentation_playlist_revision = None
        if hasattr(self, "_cached_audio_playlists"):
            delattr(self, "_cached_audio_playlists")
        if hasattr(self, "_cached_audio_playlist_details"):
            delattr(self, "_cached_audio_playlist_details")
        if hasattr(self, "_cached_media_playlists"):
            delattr(self, "_cached_media_playlists")
        if hasattr(self, "_cached_media_playlist_details"):
            delattr(self, "_cached_media_playlist_details")
        if self.streaming_coordinator:
            self.streaming_coordinator.invalidate_browsed_metadata()

    def get_presentation_playlist_catalog(self) -> dict[str, Any]:
        """Return normalized presentation playlists for an authenticated card."""
        return normalize_playlist_catalog(
            self.data.get("presentation_playlists", []),
            self.data.get("presentation_playlist_details_list", []),
        )


class ProPresenterStreamingCoordinator(DataUpdateCoordinator):
    """Streaming coordinator for frequently changing ProPresenter data."""

    def __init__(
        self,
        hass: HomeAssistant,
        api: ProPresenterAPI,
        static_coordinator: ProPresenterCoordinator = None,
    ) -> None:
        """Initialize streaming coordinator."""
        self.hass = hass
        self.api = api
        self.static_coordinator = static_coordinator
        self._stream_task = None
        self._poll_task = None
        self._stream_has_connected = False
        self.connected = False  # Track connection state globally
        self._last_logged_error = None  # Track last error to avoid log spam
        self._error_count = 0  # Count consecutive errors
        self._metadata: dict[str, Any] | None = None
        self._metadata_revision: str | None = None
        self._metadata_task: asyncio.Task[dict[str, Any] | None] | None = None
        self._metadata_lock = asyncio.Lock()
        self._browsed_metadata: dict[str, dict[str, Any]] = {}
        self._browsed_metadata_revisions: dict[str, str] = {}
        self._browsed_metadata_tasks: dict[
            str, asyncio.Task[dict[str, Any] | None]
        ] = {}
        self._browsed_metadata_lock = asyncio.Lock()
        self.thumbnail_cache = ThumbnailCache()

        # Set reference back to static coordinator
        if static_coordinator:
            static_coordinator.streaming_coordinator = self

        self._data = {
            "active_presentation": {},
            "slide_index": {},
            "active_presentation_details": None,
            "stage_screens": [],
            "stage_layouts": [],
            "layout_map": [],
            "messages": [],
            "props": [],  # Props stream instead of poll
            "looks": [],  # Looks stream instead of poll
            "current_look": {},  # Current look streams
            "status_layers": {},
            "audience_screens_status": False,
            "stage_screens_status": False,
            "stage_message": "",
            "capture_status": {},  # Capture status streams
            "timers": [],  # Timer configurations stream
            "timers_current": [],  # Current timer states stream
            "audio_transport_state": {},  # Audio transport streams
            "audio_transport_time": 0.0,
            "presentation_transport_state": {},  # Media/video transport streams
            "presentation_transport_time": 0.0,
            "active_media_playlist": {},  # Active media playlist (polled separately)
            "video_input": {},  # Current video input streams
        }

        # Initialize DataUpdateCoordinator without update_interval (no polling)
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_streaming",
            update_method=self.async_update_data,
        )

    async def async_update_data(self) -> dict[str, Any]:
        """Fetch initial data on first load, then return cached data from streaming updates."""
        # If data is still at initial state, fetch it once
        # Check if we haven't loaded messages yet (they should always be fetched initially)
        if not self._data.get("messages"):
            try:
                # Fetch all initial data in parallel for faster startup
                results = await asyncio.gather(
                    self.api.get_active_presentation(),
                    self.api.get_presentation_slide_index(),
                    self.api.get_stage_screens(),
                    self.api.get_stage_layouts(),
                    self.api.get_stage_layout_map(),
                    self.api.get_messages(),
                    self.api.get_props(),
                    self.api.get_looks(),
                    self.api.get_current_look(),
                    self.api.get_status_layers(),
                    self.api.get_audience_screens_status(),
                    self.api.get_stage_screens_status(),
                    self.api.get_stage_message(),
                    self.api.get_audio_transport_state(),
                    self.api.get_presentation_transport_state(),
                    self.api.get_active_media_playlist(),
                    return_exceptions=True,
                )

                # Unpack results (handle None values and exceptions)
                keys = [
                    "active_presentation",
                    "slide_index",
                    "stage_screens",
                    "stage_layouts",
                    "layout_map",
                    "messages",
                    "props",
                    "looks",
                    "current_look",
                    "status_layers",
                    "audience_screens_status",
                    "stage_screens_status",
                    "stage_message",
                    "audio_transport_state",
                    "presentation_transport_state",
                    "active_media_playlist",
                ]

                for i, key in enumerate(keys):
                    result = results[i]
                    if isinstance(result, Exception):
                        _LOGGER.warning(
                            "Failed to fetch %s during startup: %s", key, result
                        )
                        self._data[key] = (
                            {}
                            if key
                            in [
                                "active_presentation",
                                "slide_index",
                                "current_look",
                                "audio_transport_state",
                                "presentation_transport_state",
                                "active_media_playlist",
                                "stage_message",
                            ]
                            else []
                        )
                    else:
                        self._data[key] = result or (
                            {}
                            if key
                            in [
                                "active_presentation",
                                "slide_index",
                                "current_look",
                                "audio_transport_state",
                                "presentation_transport_state",
                                "active_media_playlist",
                                "stage_message",
                            ]
                            else []
                        )

                try:
                    await self.async_ensure_active_presentation_details()
                except Exception as err:
                    # Metadata is an optional browser capability.  Keep the
                    # existing integration available when a presentation is
                    # temporarily unavailable or the details endpoint fails.
                    _LOGGER.warning(
                        "Failed to fetch active presentation details: %s", err
                    )

            except Exception as err:
                raise UpdateFailed(f"Error fetching initial data: {err}")

        return self._data

    async def _handle_status_update(self, path: str, data: Any) -> None:
        """Handle incoming status update from stream."""
        old_uuid = self.active_presentation_uuid
        # Update data dictionary based on path (no logging for performance)
        if path == "presentation/current" or path == "presentation/active":
            self._data["active_presentation"] = data or {}
            if not data:
                # Do not let the previous slide-index payload masquerade as
                # an active presentation while ProPresenter is cleared.
                self._data["slide_index"] = {}
        elif path == "presentation/slide_index":
            self._data["slide_index"] = data or {}
        elif path == "announcement/slide_index":
            self._data["announcement_slide_index"] = data
        elif path == "stage/screens":
            self._data["stage_screens"] = data
        elif path == "stage/layouts":
            self._data["stage_layouts"] = data
        elif path == "stage/layout_map":
            self._data["layout_map"] = data
        elif path == "messages":
            self._data["messages"] = data
        elif path == "props":
            self._data["props"] = data
        elif path == "looks":
            self._data["looks"] = data
        elif path == "look/current":
            self._data["current_look"] = data
        elif path == "status/layers":
            self._data["status_layers"] = data
        elif path == "status/audience_screens":
            self._data["audience_screens_status"] = data
        elif path == "status/stage_screens":
            self._data["stage_screens_status"] = data
        elif path == "capture/status":
            self._data["capture_status"] = data
        elif path == "timers":
            self._data["timers"] = data
        elif path == "timers/current":
            self._data["timers_current"] = data
        elif path == "transport/audio/current":
            self._data["audio_transport_state"] = data
        elif path == "transport/audio/time":
            self._data["audio_transport_time"] = data
        elif path == "transport/presentation/current":
            self._data["presentation_transport_state"] = data
        elif path == "transport/presentation/time":
            self._data["presentation_transport_time"] = data
        elif path == "stage/message":
            self._data["stage_message"] = data

        new_uuid = self.active_presentation_uuid
        if path in {
            "presentation/current",
            "presentation/active",
            "presentation/slide_index",
        }:
            if new_uuid != old_uuid:
                if new_uuid:
                    self._browsed_metadata.pop(new_uuid, None)
                    self._browsed_metadata_revisions.pop(new_uuid, None)
                self._invalidate_metadata()
                self._schedule_metadata_refresh()

        # Notify listeners that data has changed
        self.async_set_updated_data(self._data)

    @property
    def active_presentation_uuid(self) -> str | None:
        """Return the UUID currently reported by ProPresenter."""
        return get_presentation_uuid(
            self._data.get("active_presentation")
        ) or get_presentation_uuid(self._data.get("slide_index"))

    @property
    def metadata_revision(self) -> str | None:
        """Return the opaque generation token for the current metadata."""
        return self._metadata_revision

    @property
    def metadata(self) -> dict[str, Any] | None:
        """Return normalized active presentation metadata, if available."""
        return self._metadata

    @property
    def metadata_available(self) -> bool:
        """Whether normalized metadata matches the currently active UUID."""
        return bool(
            self._metadata
            and self._metadata_revision
            and self._metadata.get("uuid") == self.active_presentation_uuid
        )

    @property
    def slide_layer_active(self) -> bool:
        """Whether ProPresenter's slide output layer is currently visible."""
        return bool(self._data.get("status_layers", {}).get("slide", False))

    def get_active_snapshot(self) -> dict[str, Any]:
        """Return scalar active state for the stable Home Assistant sensor."""
        slide_index_data = self._data.get("slide_index") or {}
        presentation_index = slide_index_data.get("presentation_index")
        current_index = get_slide_index(slide_index_data)
        active_data = self._data.get("active_presentation") or {}
        presentation_uuid = self.active_presentation_uuid
        name = (
            get_presentation_name(active_data)
            or get_presentation_name(presentation_index)
            or (
                self._metadata.get("name")
                if self._metadata and self._metadata.get("uuid") == presentation_uuid
                else None
            )
        )
        current_slide = find_slide(self._metadata, current_index)
        current_group = find_slide_group(self._metadata, current_index)
        return {
            "presentation_uuid": presentation_uuid,
            "current_index": current_index,
            "current_label": current_slide.get("label") if current_slide else None,
            "current_group": current_group.get("label") if current_group else None,
            "slide_count": self._metadata.get("slide_count", 0)
            if self.metadata_available
            else 0,
            "metadata_revision": self._metadata_revision
            if self.metadata_available
            else None,
            "slide_layer_active": self.slide_layer_active,
            "metadata_available": self.metadata_available,
            "name": name,
        }

    def get_presentation_metadata(
        self, presentation_uuid: str | None
    ) -> dict[str, Any] | None:
        """Return normalized metadata for the active or selected presentation."""
        if not presentation_uuid:
            return None
        if presentation_uuid == self.active_presentation_uuid:
            return self._metadata if self.metadata_available else None
        if not self.is_known_presentation_uuid(presentation_uuid):
            return None
        return self._browsed_metadata.get(presentation_uuid)

    def get_presentation_revision(self, presentation_uuid: str | None) -> str | None:
        """Return the opaque revision for one active or browsed presentation."""
        if not presentation_uuid:
            return None
        if presentation_uuid == self.active_presentation_uuid:
            return self._metadata_revision if self.metadata_available else None
        if not self.is_known_presentation_uuid(presentation_uuid):
            return None
        return self._browsed_metadata_revisions.get(presentation_uuid)

    def is_known_presentation_uuid(self, presentation_uuid: str | None) -> bool:
        """Whether a UUID is active or appears in this entry's playlist catalog."""
        if not presentation_uuid:
            return False
        if presentation_uuid == self.active_presentation_uuid:
            return True
        if not self.static_coordinator:
            return False
        if not self.static_coordinator.data.get("presentation_playlist_revision"):
            return False
        catalog = self.static_coordinator.get_presentation_playlist_catalog()
        known_uuids = set(catalog.get("presentation_uuids", []))
        stale_uuids = set(self._browsed_metadata) - known_uuids
        if stale_uuids:
            for stale_uuid in stale_uuids:
                self._browsed_metadata.pop(stale_uuid, None)
                self._browsed_metadata_revisions.pop(stale_uuid, None)
            self._sync_thumbnail_identities()
        return presentation_uuid in known_uuids

    def _sync_thumbnail_identities(self) -> None:
        """Retain only current revisions for active and browsed presentations."""
        identities: set[tuple[str, str]] = set()
        if self._metadata and self._metadata_revision:
            identities.add((self._metadata["uuid"], self._metadata_revision))
        identities.update(self._browsed_metadata_revisions.items())
        self.thumbnail_cache.set_allowed_identities(identities)

    def invalidate_browsed_metadata(self) -> None:
        """Drop playlist presentation metadata after the playlist catalog changes."""
        for task in self._browsed_metadata_tasks.values():
            if not task.done():
                task.cancel()
        self._browsed_metadata.clear()
        self._browsed_metadata_revisions.clear()
        self._sync_thumbnail_identities()

    def build_metadata_response(
        self, entity_id: str, presentation_uuid: str | None = None
    ) -> dict[str, Any]:
        """Build the wire response consumed by the companion card."""
        active_snapshot = self.get_active_snapshot()
        selected_uuid = presentation_uuid or active_snapshot["presentation_uuid"]
        metadata = self.get_presentation_metadata(selected_uuid)
        revision = self.get_presentation_revision(selected_uuid)
        is_active = selected_uuid == active_snapshot["presentation_uuid"]
        selected_index = active_snapshot["current_index"] if is_active else None
        selected_slide = find_slide(metadata, selected_index)
        selected_group = find_slide_group(metadata, selected_index)
        return {
            "protocol_version": 1,
            "entity_id": entity_id,
            "metadata_revision": revision,
            "presentation_uuid": selected_uuid,
            "presentation_name": metadata.get("name") if metadata else None,
            "current_slide_index": selected_index,
            "current_slide_label": selected_slide.get("label")
            if selected_slide
            else None,
            "current_group": selected_group.get("label") if selected_group else None,
            "slide_count": metadata.get("slide_count", 0) if metadata else 0,
            # This is always the actual output layer, even while the operator
            # is browsing a non-live playlist item.
            "slide_layer_active": active_snapshot["slide_layer_active"],
            "metadata_available": bool(metadata and revision),
            "is_active_presentation": is_active,
            "active_presentation_uuid": active_snapshot["presentation_uuid"],
            "active_slide_index": active_snapshot["current_index"],
            "active_metadata_revision": active_snapshot["metadata_revision"],
            "groups": metadata.get("groups", []) if metadata else [],
        }

    async def async_ensure_presentation_details(
        self, presentation_uuid: str, *, refresh: bool = False
    ) -> dict[str, Any] | None:
        """Fetch metadata for the active or an allowed playlist presentation."""
        if presentation_uuid == self.active_presentation_uuid:
            return await self.async_ensure_active_presentation_details(refresh=refresh)
        if not self.is_known_presentation_uuid(presentation_uuid):
            raise LookupError("Presentation is not present in the configured playlists")

        if not refresh and presentation_uuid in self._browsed_metadata:
            return self._browsed_metadata[presentation_uuid]

        task = self._browsed_metadata_tasks.get(presentation_uuid)
        if task and not task.done():
            return await asyncio.shield(task)

        task = self.hass.async_create_task(
            self._async_fetch_browsed_presentation_details(presentation_uuid)
        )
        self._browsed_metadata_tasks[presentation_uuid] = task
        try:
            return await asyncio.shield(task)
        finally:
            if self._browsed_metadata_tasks.get(presentation_uuid) is task:
                self._browsed_metadata_tasks.pop(presentation_uuid, None)

    async def _async_fetch_browsed_presentation_details(
        self, presentation_uuid: str
    ) -> dict[str, Any] | None:
        """Fetch a playlist presentation and commit only current identities."""
        async with self._browsed_metadata_lock:
            details = await self.api.get_presentation_details(presentation_uuid)
            if not details:
                raise ProPresenterConnectionError(
                    f"No details returned for presentation {presentation_uuid}"
                )
            if not self.is_known_presentation_uuid(presentation_uuid):
                _LOGGER.debug(
                    "Discarding details for removed playlist presentation %s",
                    presentation_uuid,
                )
                return None

            normalized = normalize_presentation(details, presentation_uuid)
            self._browsed_metadata[presentation_uuid] = normalized
            self._browsed_metadata_revisions[presentation_uuid] = secrets.token_urlsafe(
                24
            )
            self._sync_thumbnail_identities()
            return normalized

    async def async_ensure_active_presentation_details(
        self, *, refresh: bool = False
    ) -> dict[str, Any] | None:
        """Fetch and normalize details, sharing concurrent requests."""
        if self._metadata_task and not self._metadata_task.done():
            return await asyncio.shield(self._metadata_task)

        current_uuid = self.active_presentation_uuid
        if (
            not refresh
            and self.metadata_available
            and self._metadata
            and self._metadata.get("uuid") == current_uuid
        ):
            return self._metadata

        task = self.hass.async_create_task(
            self._async_fetch_active_presentation_details()
        )
        self._metadata_task = task
        try:
            result = await asyncio.shield(task)
        finally:
            if self._metadata_task is task and task.done():
                self._metadata_task = None
        if result is None and self.active_presentation_uuid:
            return await self.async_ensure_active_presentation_details(refresh=True)
        return result

    async def _async_fetch_active_presentation_details(self) -> dict[str, Any] | None:
        """Fetch details and commit them only if the active UUID is unchanged."""
        async with self._metadata_lock:
            presentation_uuid = self.active_presentation_uuid
            if not presentation_uuid:
                self._invalidate_metadata(clear_task=False)
                self.async_set_updated_data(self._data)
                return None

            details = await self.api.get_presentation_details(presentation_uuid)
            if not details:
                raise ProPresenterConnectionError(
                    f"No details returned for presentation {presentation_uuid}"
                )

            if self.active_presentation_uuid != presentation_uuid:
                _LOGGER.debug(
                    "Discarding details for stale presentation %s", presentation_uuid
                )
                return None

            normalized = normalize_presentation(details, presentation_uuid)
            self._metadata = normalized
            self._metadata_revision = secrets.token_urlsafe(24)
            self._data["active_presentation_details"] = normalized
            self._sync_thumbnail_identities()
            self.async_set_updated_data(self._data)
            return normalized

    def _invalidate_metadata(self, *, clear_task: bool = True) -> None:
        """Drop old metadata and bytes before a new UUID/revision can be used."""
        active_uuid = self.active_presentation_uuid
        if active_uuid:
            self._browsed_metadata.pop(active_uuid, None)
            self._browsed_metadata_revisions.pop(active_uuid, None)
        self._metadata = None
        self._metadata_revision = None
        self._data["active_presentation_details"] = None
        self._sync_thumbnail_identities()
        if clear_task and self._metadata_task and not self._metadata_task.done():
            self._metadata_task.cancel()
            self._metadata_task = None

    def _schedule_metadata_refresh(self) -> None:
        """Refresh metadata after a stream update without blocking the stream."""

        async def refresh() -> None:
            try:
                await self.async_ensure_active_presentation_details(refresh=True)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                _LOGGER.warning(
                    "Unable to refresh active presentation metadata: %s", err
                )

        self.hass.async_create_task(refresh())

    async def async_refresh_active_presentation(self) -> dict[str, Any] | None:
        """Explicitly refetch active metadata and invalidate its thumbnails."""
        self._invalidate_metadata()
        self.async_set_updated_data(self._data)
        return await self.async_ensure_active_presentation_details(refresh=True)

    async def async_reconcile_active_presentation(
        self, *, force_metadata_refresh: bool = True
    ) -> None:
        """Reconcile active UUID, slide index, and metadata after reconnect."""
        active, slide_index = await asyncio.gather(
            self.api.get_active_presentation(),
            self.api.get_presentation_slide_index(),
        )
        old_uuid = self.active_presentation_uuid
        self._data["active_presentation"] = active or {}
        self._data["slide_index"] = slide_index or {}
        if self.active_presentation_uuid != old_uuid or force_metadata_refresh:
            # A reconnect is an explicit freshness boundary even when the same
            # presentation is still active.
            self._invalidate_metadata()
        await self.async_ensure_active_presentation_details(
            refresh=force_metadata_refresh
        )
        self.async_set_updated_data(self._data)

    async def async_get_thumbnail(
        self,
        presentation_uuid: str,
        revision: str,
        slide_index: int,
        quality: int,
    ) -> bytes | None:
        """Get one authenticated-view thumbnail through the shared cache."""
        metadata = self.get_presentation_metadata(presentation_uuid)
        current_revision = self.get_presentation_revision(presentation_uuid)
        if not metadata or not current_revision:
            return None
        if (
            revision != current_revision
            or slide_index < 0
            or slide_index >= metadata.get("slide_count", 0)
            or find_slide(metadata, slide_index) is None
        ):
            return None
        key: ThumbnailKey = (presentation_uuid, revision, slide_index, quality)
        self._sync_thumbnail_identities()
        return await self.thumbnail_cache.get_or_fetch(
            key,
            lambda: self.api.get_presentation_thumbnail(
                presentation_uuid, slide_index, quality=quality
            ),
        )

    async def start_streaming(self) -> None:
        """Start the streaming connection."""
        if self._stream_task and not self._stream_task.done():
            return

        # Create background tasks that don't block HA startup
        # Using asyncio.create_task instead of hass.async_create_task
        # so HA doesn't wait for them during startup
        self._stream_task = asyncio.create_task(self._run_stream())

        # Start polling task for active media playlist
        self._poll_task = asyncio.create_task(self._poll_active_playlist())

    async def _poll_active_playlist(self) -> None:
        """Poll for active media playlist changes"""
        while True:
            try:
                await asyncio.sleep(2)  # Poll every 2 seconds
                active_media = await self.api.get_active_media_playlist() or {}

                # Only update if it changed
                if active_media != self._data.get("active_media_playlist"):
                    self._data["active_media_playlist"] = active_media
                    self.async_set_updated_data(self._data)
            except Exception:
                await asyncio.sleep(5)

    async def _run_stream(self) -> None:
        """Run the streaming connection (with auto-reconnect)."""
        reconnect_delay = 5  # Start with 5 second delay
        max_reconnect_delay = 30  # Max 30 seconds between attempts

        while True:
            try:
                # Reset delay on successful connection
                reconnect_delay = 5

                await self.api.stream_status_updates(
                    [
                        "presentation/current",
                        "presentation/slide_index",
                        "announcement/slide_index",
                        "stage/screens",
                        "stage/layouts",
                        "stage/layout_map",
                        "messages",
                        "props",  # Props stream (no polling)
                        "looks",  # Looks stream (no polling)
                        "look/current",  # Current look streams
                        "status/layers",
                        "status/audience_screens",
                        "status/stage_screens",
                        "capture/status",  # Capture status streams
                        "timers",  # Timer configurations stream
                        "timers/current",  # Timer states stream
                        "transport/audio/current",  # Audio transport state
                        "transport/audio/time",  # Audio transport time
                        "transport/presentation/current",  # Media/video transport state
                        "transport/presentation/time",  # Media/video transport time
                        "stage/message",
                    ],
                    self._handle_status_update,
                    on_connected=self._handle_stream_connected,
                )
                raise ProPresenterConnectionError("Status update stream ended")
            except asyncio.CancelledError:
                raise
            except Exception as err:
                error_msg = str(err) if err else "Connection lost"

                # Rate limit error logging - only log if error message changed or error count reaches threshold
                should_log = False
                if error_msg != self._last_logged_error:
                    # New error type - always log it
                    should_log = True
                    self._error_count = 1
                    self._last_logged_error = error_msg
                elif self._error_count == 1:
                    # Log the first repeat of the same error
                    should_log = True
                    self._error_count += 1
                elif self._error_count % 10 == 0:
                    # Then log every 10th occurrence
                    should_log = True
                    self._error_count += 1
                else:
                    self._error_count += 1

                # Check if this might be an unsupported version issue (400 Bad Request on streaming)
                version_hint = ""
                if "400" in error_msg and self.static_coordinator:
                    try:
                        version_data = self.static_coordinator.data.get("version", {})
                        host_description = version_data.get("host_description", "")
                        if host_description:
                            version_hint = f" - Current version: {host_description}. If using v7.9 or below, the /v1/status/updates endpoint is not supported. Please upgrade to v7.9.1 or higher."
                    except Exception:
                        pass

                if should_log:
                    _LOGGER.warning(
                        "Stream disconnected: %s. Reconnecting in %d seconds...%s",
                        error_msg,
                        reconnect_delay,
                        version_hint,
                    )

                # Mark entities as unavailable when disconnected
                self.connected = False
                self.last_update_success = False
                self.async_update_listeners()
                # Also mark static coordinator unavailable
                if self.static_coordinator:
                    self.static_coordinator.last_update_success = False
                    self.static_coordinator.async_update_listeners()

                await asyncio.sleep(reconnect_delay)

                # Exponential backoff for reconnection attempts
                reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)

    async def _handle_stream_connected(self) -> None:
        """Reconcile state once the long-lived status stream is established."""
        self.connected = True
        self.last_update_success = True
        try:
            await self.async_reconcile_active_presentation(
                force_metadata_refresh=self._stream_has_connected
            )
            self._stream_has_connected = True
        except Exception as err:
            _LOGGER.warning("Could not reconcile ProPresenter after reconnect: %s", err)

        if self.static_coordinator:
            try:
                await self.static_coordinator.async_refresh()
            except Exception as err:
                _LOGGER.debug(
                    "Could not refresh static coordinator on reconnect: %s", err
                )
        self.async_update_listeners()

    async def async_shutdown(self) -> None:
        """Stop the streaming connection."""
        if self._metadata_task and not self._metadata_task.done():
            self._metadata_task.cancel()
            try:
                await self._metadata_task
            except asyncio.CancelledError:
                pass

        for task in self._browsed_metadata_tasks.values():
            if not task.done():
                task.cancel()
        if self._browsed_metadata_tasks:
            await asyncio.gather(
                *self._browsed_metadata_tasks.values(), return_exceptions=True
            )
        self._browsed_metadata_tasks.clear()

        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass

        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
