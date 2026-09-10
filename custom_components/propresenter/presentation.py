"""Presentation metadata normalization for the ProPresenter browser.

The ProPresenter API exposes presentation details as a nested document.  This
module turns that document into a deliberately small, JSON-friendly model that
can be sent to a dashboard without creating one Home Assistant entity per
slide.
"""

from __future__ import annotations

from typing import Any

from .utils import generate_slide_label


def _as_text(value: Any) -> str | None:
    """Return a useful string value, or ``None`` for an empty value."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    if value is None:
        return None
    return str(value)


def _identity(value: Any) -> dict[str, Any]:
    """Return the identity dictionary from one of ProPresenter's shapes."""
    if not isinstance(value, dict):
        return {}
    nested = value.get("id")
    if isinstance(nested, dict):
        return nested
    return value


def _presentation_document(details: dict[str, Any] | None) -> dict[str, Any]:
    """Get the presentation document regardless of the API wrapper shape."""
    if not isinstance(details, dict):
        return {}
    presentation = details.get("presentation")
    return presentation if isinstance(presentation, dict) else details


def get_presentation_uuid(data: Any) -> str | None:
    """Extract a presentation UUID from active/index API responses."""
    if not isinstance(data, dict):
        return None

    presentation_index = data.get("presentation_index")
    candidates: list[Any] = [
        data,
        presentation_index,
        presentation_index.get("presentation_id")
        if isinstance(presentation_index, dict)
        else None,
        data.get("presentation"),
        data.get("presentation_id"),
        data.get("current_presentation"),
        data.get("id"),
    ]
    for candidate in candidates:
        identity = _identity(candidate)
        uuid = _as_text(identity.get("uuid"))
        if uuid:
            return uuid
    return None


def get_presentation_name(data: Any) -> str | None:
    """Extract a presentation name from an active/index API response."""
    if not isinstance(data, dict):
        return None

    presentation_index = data.get("presentation_index")
    candidates: list[Any] = [
        data,
        presentation_index,
        presentation_index.get("presentation_id")
        if isinstance(presentation_index, dict)
        else None,
        data.get("presentation"),
        data.get("presentation_id"),
        data.get("current_presentation"),
        data.get("id"),
    ]
    for candidate in candidates:
        identity = _identity(candidate)
        name = _as_text(identity.get("name"))
        if name:
            return name
    return None


def get_slide_index(data: Any) -> int | None:
    """Extract the 0-based active slide index from a status response."""
    if not isinstance(data, dict):
        return None
    presentation_index = data.get("presentation_index")
    if isinstance(presentation_index, dict):
        value = presentation_index.get("index")
    else:
        value = data.get("index")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _color(value: Any) -> Any:
    """Keep ProPresenter's color value without imposing a new format."""
    if isinstance(value, (str, int, float, dict, list)):
        return value
    return None


def _slide_notes(slide: dict[str, Any]) -> str | None:
    """Preserve plain or HTML slide notes when they are present."""
    for key in ("notes", "notes_html"):
        value = slide.get(key)
        if value is not None:
            return value if isinstance(value, str) else str(value)
    return None


def _group_label(group: dict[str, Any], ordinal: int) -> str:
    """Return a stable human-facing group label with a safe fallback."""
    identity = _identity(group)
    for value in (
        group.get("name"),
        group.get("label"),
        identity.get("name"),
    ):
        label = _as_text(value)
        if label:
            return label
    return f"Group {ordinal + 1}"


def normalize_presentation(
    details: dict[str, Any], presentation_uuid: str | None = None
) -> dict[str, Any]:
    """Normalize presentation details while preserving source order and indexes.

    Group occurrence keys intentionally contain the source identity *and* the
    occurrence ordinal.  A presentation with two groups both named
    ``Chorus`` therefore keeps two distinct groups.  Slide indexes count every
    source slide, including disabled slides, so they remain valid API targets.
    """
    presentation = _presentation_document(details)
    identity = _identity(presentation)
    normalized_uuid = (
        presentation_uuid
        or _as_text(identity.get("uuid"))
        or get_presentation_uuid(details)
        or ""
    )
    name = (
        _as_text(presentation.get("name"))
        or _as_text(identity.get("name"))
        or "Untitled presentation"
    )

    raw_groups = presentation.get("groups")
    if not isinstance(raw_groups, list):
        raw_groups = []

    # A few older ProPresenter responses expose slides directly.  Treat that
    # shape as one source group rather than dropping the slides.
    if not raw_groups and isinstance(presentation.get("slides"), list):
        raw_groups = [{"name": "Slides", "slides": presentation["slides"]}]

    groups: list[dict[str, Any]] = []
    slide_index = 0
    source_occurrences: dict[str, int] = {}

    for group_ordinal, raw_group in enumerate(raw_groups):
        if not isinstance(raw_group, dict):
            continue
        group_identity = _identity(raw_group)
        source_key = (
            _as_text(group_identity.get("uuid"))
            or _as_text(raw_group.get("uuid"))
            or f"ordinal-{group_ordinal}"
        )
        occurrence = source_occurrences.get(source_key, 0)
        source_occurrences[source_key] = occurrence + 1
        group_key = f"{source_key}#{occurrence}"
        group_label = _group_label(raw_group, group_ordinal)
        group_slides = raw_group.get("slides")
        if not isinstance(group_slides, list):
            group_slides = []

        slides: list[dict[str, Any]] = []
        for raw_slide in group_slides:
            if not isinstance(raw_slide, dict):
                continue
            enabled = raw_slide.get("enabled", True)
            if not isinstance(enabled, bool):
                enabled = bool(enabled)
            slides.append(
                {
                    "index": slide_index,
                    "group_key": group_key,
                    "label": generate_slide_label(raw_slide, slide_index),
                    "notes": _slide_notes(raw_slide),
                    "enabled": enabled,
                    "color": _color(raw_slide.get("color")),
                }
            )
            slide_index += 1

        groups.append(
            {
                "key": group_key,
                "label": group_label,
                "color": _color(raw_group.get("color")),
                "slides": slides,
            }
        )

    return {
        "uuid": normalized_uuid,
        "name": name,
        "slide_count": slide_index,
        "groups": groups,
    }


def find_slide(
    metadata: dict[str, Any] | None, slide_index: int | None
) -> dict[str, Any] | None:
    """Find a normalized slide by its original 0-based API index."""
    if not metadata or slide_index is None:
        return None
    for group in metadata.get("groups", []):
        for slide in group.get("slides", []):
            if slide.get("index") == slide_index:
                return slide
    return None


def find_slide_group(
    metadata: dict[str, Any] | None, slide_index: int | None
) -> dict[str, Any] | None:
    """Find the group containing a normalized slide."""
    if not metadata or slide_index is None:
        return None
    for group in metadata.get("groups", []):
        if any(slide.get("index") == slide_index for slide in group.get("slides", [])):
            return group
    return None
