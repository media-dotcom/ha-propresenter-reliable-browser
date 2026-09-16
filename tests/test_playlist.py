"""Pure tests for presentation playlist normalization."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types
import unittest

PACKAGE_ROOT = Path(__file__).parents[1] / "custom_components" / "propresenter"
PURE_PACKAGE = "propresenter_playlist_test"
package = types.ModuleType(PURE_PACKAGE)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PURE_PACKAGE] = package
spec = spec_from_file_location(f"{PURE_PACKAGE}.playlist", PACKAGE_ROOT / "playlist.py")
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)

from propresenter_playlist_test.playlist import (  # noqa: E402
    find_playlist_presentation,
    normalize_playlist_catalog,
)


class PlaylistNormalizationTest(unittest.TestCase):
    def test_joins_nested_playlists_and_preserves_repeated_occurrences(self) -> None:
        catalog = normalize_playlist_catalog(
            [
                {
                    "field_type": "group",
                    "children": [
                        {
                            "field_type": "playlist",
                            "id": {"uuid": "playlist-1", "name": "Sunday"},
                        }
                    ],
                },
                {
                    "field_type": "playlist",
                    "id": {"uuid": "playlist-2", "name": "Archive"},
                },
            ],
            [
                {
                    "id": {"uuid": "playlist-1", "name": "Sunday Service"},
                    "items": [
                        {
                            "type": "presentation",
                            "id": {"uuid": "pres-1", "name": "Opening Song"},
                        },
                        {
                            "type": "group",
                            "id": {"uuid": "group-1", "name": "Songs"},
                            "children": [
                                {
                                    "type": "presentation",
                                    "id": {
                                        "uuid": "pres-2",
                                        "name": "Sermon",
                                    },
                                }
                            ],
                        },
                        {
                            "type": "presentation",
                            "id": {"uuid": "pres-1", "name": "Opening Song"},
                        },
                    ],
                },
                {"id": {"uuid": "playlist-2", "name": "Archive"}, "items": []},
            ],
        )

        self.assertEqual(
            [playlist["name"] for playlist in catalog["playlists"]],
            ["Sunday Service", "Archive"],
        )
        items = catalog["playlists"][0]["items"]
        self.assertEqual(
            [item["presentation_uuid"] for item in items],
            ["pres-1", "pres-2", "pres-1"],
        )
        self.assertNotEqual(items[0]["key"], items[2]["key"])
        self.assertEqual(items[1]["path"], "Songs")
        self.assertEqual(items[1]["index"], 1)
        self.assertEqual(catalog["presentation_uuids"], ["pres-1", "pres-2"])

    def test_finds_allowed_presentation_by_uuid(self) -> None:
        catalog = {
            "playlists": [
                {
                    "uuid": "playlist",
                    "name": "Service",
                    "items": [
                        {
                            "key": "playlist:0:pres",
                            "uuid": "pres",
                            "presentation_uuid": "pres",
                            "name": "Song",
                        }
                    ],
                }
            ]
        }
        self.assertEqual(find_playlist_presentation(catalog, "pres")["name"], "Song")
        self.assertIsNone(find_playlist_presentation(catalog, "missing"))

    def test_accepts_inline_playlist_details_without_a_type_field(self) -> None:
        catalog = normalize_playlist_catalog(
            [
                {
                    "id": {"uuid": "playlist", "name": "Inline Service"},
                    "items": [
                        {"id": {"uuid": "pres", "name": "Song"}},
                    ],
                }
            ],
            [],
        )
        self.assertEqual(catalog["playlists"][0]["name"], "Inline Service")
        self.assertEqual(catalog["playlists"][0]["items"][0]["uuid"], "pres")

    def test_filters_non_presentation_items_and_unwraps_payloads(self) -> None:
        catalog = normalize_playlist_catalog(
            {
                "playlists": [
                    {
                        "id": {"uuid": "playlist", "name": "Service"},
                        "type": "playlist",
                    }
                ]
            },
            {
                "data": {
                    "id": {"uuid": "playlist", "name": "Service"},
                    "items": [
                        {
                            "id": {"uuid": "header", "name": "Songs"},
                            "type": "header",
                        },
                        {
                            "id": {"uuid": "song", "name": "Song"},
                            "type": "presentation",
                        },
                        {
                            "id": {"uuid": "video", "name": "Video"},
                            "type": "media",
                        },
                        {
                            "id": {"uuid": "placeholder", "name": "Unlinked"},
                            "type": "placeholder",
                        },
                    ],
                }
            },
        )

        self.assertEqual(
            [item["presentation_uuid"] for item in catalog["playlists"][0]["items"]],
            ["song"],
        )
        self.assertEqual(catalog["presentation_uuids"], ["song"])


if __name__ == "__main__":
    unittest.main()
