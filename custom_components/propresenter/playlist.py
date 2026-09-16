"""Normalize ProPresenter presentation playlists for dashboard browsing."""

from __future__ import annotations

from typing import Any, Iterator


def _as_text(value: Any) -> str | None:
    """Return a useful string value, or ``None`` for an empty value."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if value is None:
        return None
    return str(value)


def _identity(value: Any) -> dict[str, Any]:
    """Return an identity dictionary from one of ProPresenter's shapes."""
    if not isinstance(value, dict):
        return {}
    nested = value.get("id")
    if isinstance(nested, dict):
        return nested
    return value


def _identity_for_item(item: dict[str, Any]) -> dict[str, Any]:
    """Find the most specific identity in a playlist item."""
    for key in ("presentation", "presentation_id", "id"):
        value = item.get(key)
        identity = _identity(value)
        if identity.get("uuid") or identity.get("name"):
            return identity
    return {}


def _uuid(value: Any) -> str | None:
    """Extract one UUID-like value from an API object."""
    identity = _identity(value)
    return _as_text(identity.get("uuid"))


def _name(value: Any, fallback: str) -> str:
    """Extract a display name from an API object."""
    identity = _identity(value)
    return _as_text(identity.get("name")) or _as_text(identity.get("label")) or fallback


def _kind(item: dict[str, Any]) -> str:
    """Return the lower-case ProPresenter item kind."""
    for key in ("field_type", "type", "item_type"):
        value = _as_text(item.get(key))
        if value:
            return value.lower()
    return "presentation"


def _children(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Return nested playlist children from either known response shape."""
    for key in ("children", "items"):
        value = item.get(key)
        if isinstance(value, list):
            return [child for child in value if isinstance(child, dict)]
    return []


def _is_container(item: dict[str, Any]) -> bool:
    """Whether an item contains more playlist items instead of a presentation."""
    kind = _kind(item)
    return kind in {"playlist", "group", "folder", "collection"} or (
        bool(_children(item)) and not _uuid(_identity_for_item(item))
    )


def _playlist_refs(items: list[dict[str, Any]]) -> Iterator[dict[str, str]]:
    """Yield playlist identities in source order, including nested playlists."""
    for item in items:
        kind = _kind(item)
        playlist_uuid = _uuid(item)
        has_item_list = isinstance(item.get("items"), list)
        has_explicit_kind = any(
            _as_text(item.get(key)) for key in ("field_type", "type", "item_type")
        )
        if playlist_uuid and (
            kind in {"playlist", "folder", "collection"}
            or (has_item_list and not has_explicit_kind)
            or (has_item_list and kind not in {"presentation", "slide", "group"})
        ):
            yield {
                "uuid": playlist_uuid,
                "name": _name(item, "Untitled playlist"),
            }
        children = _children(item)
        if children:
            yield from _playlist_refs(children)


def _playlist_items(
    items: list[dict[str, Any]], playlist_uuid: str
) -> list[dict[str, Any]]:
    """Flatten presentation items while preserving their source occurrence."""
    result: list[dict[str, Any]] = []

    def walk(
        nested_items: list[dict[str, Any]],
        path: tuple[int, ...],
        labels: tuple[str, ...],
    ) -> None:
        for source_index, item in enumerate(nested_items):
            item_path = path + (source_index,)
            if _is_container(item):
                label = _name(item, "Group")
                walk(_children(item), item_path, labels + (label,))
                continue

            identity = _identity_for_item(item)
            item_uuid = _uuid(identity)
            if not item_uuid:
                continue

            item_kind = _kind(item)
            if item_kind in {"playlist", "folder", "collection", "group"}:
                continue

            item_name = _name(item, f"Presentation {len(result) + 1}")
            result.append(
                {
                    "key": (
                        f"{playlist_uuid}:{'.'.join(str(part) for part in item_path)}:"
                        f"{item_uuid}"
                    ),
                    "uuid": item_uuid,
                    "presentation_uuid": item_uuid,
                    "name": item_name,
                    "index": item_path[0],
                    "path": " / ".join(labels) if labels else None,
                    "type": item_kind or "presentation",
                }
            )

    walk(items, (), ())
    return result


def normalize_playlist_catalog(
    playlists: list[dict[str, Any]] | None,
    playlist_details: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    """Build a compact, stable playlist catalog for a dashboard selector.

    The API exposes the playlist tree separately from each playlist's item
    details.  This joins the two responses, preserves their source order, and
    keeps repeated occurrences distinct by giving every item an occurrence
    key.  Only presentation-bearing items are returned to the card.
    """
    root_items = [item for item in playlists or [] if isinstance(item, dict)]
    details_items = [item for item in playlist_details or [] if isinstance(item, dict)]

    details_by_uuid = {
        playlist_uuid: details
        for details in details_items
        if (playlist_uuid := _uuid(details))
    }
    # A few API versions include the playlist's items in the catalog response
    # itself.  Keep that shape usable even when a separate detail response was
    # not returned.
    for item in root_items:
        playlist_uuid = _uuid(item)
        if playlist_uuid and isinstance(item.get("items"), list):
            details_by_uuid.setdefault(playlist_uuid, item)

    refs: list[dict[str, str]] = []
    seen: set[str] = set()
    for ref in _playlist_refs(root_items):
        if ref["uuid"] not in seen:
            refs.append(ref)
            seen.add(ref["uuid"])

    # Some ProPresenter versions return details without a matching tree entry.
    # Include those playlists at the end instead of making their presentations
    # unreachable from the browser.
    for details in details_items:
        playlist_uuid = _uuid(details)
        if playlist_uuid and playlist_uuid not in seen:
            refs.append(
                {
                    "uuid": playlist_uuid,
                    "name": _name(details, "Untitled playlist"),
                }
            )
            seen.add(playlist_uuid)

    normalized_playlists: list[dict[str, Any]] = []
    presentation_uuids: list[str] = []
    seen_presentations: set[str] = set()

    for ref in refs:
        details = details_by_uuid.get(ref["uuid"], {})
        raw_items = details.get("items")
        if not isinstance(raw_items, list):
            raw_items = details.get("children")
        if not isinstance(raw_items, list):
            raw_items = []
        items = _playlist_items(raw_items, ref["uuid"])
        playlist = {
            "uuid": ref["uuid"],
            "name": _name(details, ref["name"]),
            "items": items,
        }
        normalized_playlists.append(playlist)
        for item in items:
            item_uuid = item["presentation_uuid"]
            if item_uuid not in seen_presentations:
                presentation_uuids.append(item_uuid)
                seen_presentations.add(item_uuid)

    return {
        "playlists": normalized_playlists,
        "presentation_uuids": presentation_uuids,
    }


def find_playlist_presentation(
    catalog: dict[str, Any] | None, presentation_uuid: str
) -> dict[str, Any] | None:
    """Find a presentation item in a normalized catalog."""
    if not catalog:
        return None
    for playlist in catalog.get("playlists", []):
        for item in playlist.get("items", []):
            if item.get("presentation_uuid") == presentation_uuid:
                return item
    return None
