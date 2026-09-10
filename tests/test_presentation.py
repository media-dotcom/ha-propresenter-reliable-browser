"""Pure tests for dynamic presentation normalization."""

from __future__ import annotations

import unittest
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

PACKAGE_ROOT = Path(__file__).parents[1] / "custom_components" / "propresenter"
PURE_PACKAGE = "propresenter_pure_test"
package = types.ModuleType(PURE_PACKAGE)
package.__path__ = [str(PACKAGE_ROOT)]
sys.modules[PURE_PACKAGE] = package
for module_name in ("utils", "presentation"):
    spec = spec_from_file_location(
        f"{PURE_PACKAGE}.{module_name}", PACKAGE_ROOT / f"{module_name}.py"
    )
    module = module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

from propresenter_pure_test.presentation import (  # noqa: E402
    find_slide,
    find_slide_group,
    get_presentation_uuid,
    get_slide_index,
    normalize_presentation,
)


def _slide(label: str, *, enabled: bool = True, notes: str | None = None) -> dict:
    return {
        "label": label,
        "text": label,
        "enabled": enabled,
        "notes": notes,
        "color": "#ffffff",
    }


class PresentationNormalizationTest(unittest.TestCase):
    def test_repeated_groups_disabled_slides_and_original_indexes(self) -> None:
        details = {
            "presentation": {
                "id": {"uuid": "pres-1", "name": "Song"},
                "groups": [
                    {
                        "id": {"uuid": "chorus"},
                        "name": "Chorus",
                        "color": "blue",
                        "slides": [_slide("First")],
                    },
                    {
                        "id": {"uuid": "chorus"},
                        "name": "Chorus",
                        "color": "green",
                        "slides": [
                            _slide("Disabled", enabled=False),
                            _slide("Third", notes="speaker note"),
                        ],
                    },
                ],
            }
        }

        normalized = normalize_presentation(details)
        self.assertEqual(normalized["uuid"], "pres-1")
        self.assertEqual(normalized["slide_count"], 3)
        self.assertEqual(
            [group["key"] for group in normalized["groups"]],
            ["chorus#0", "chorus#1"],
        )
        slides = [slide for group in normalized["groups"] for slide in group["slides"]]
        self.assertEqual([slide["index"] for slide in slides], [0, 1, 2])
        self.assertFalse(slides[1]["enabled"])
        self.assertEqual(slides[2]["notes"], "speaker note")
        self.assertEqual(normalized["groups"][0]["color"], "blue")

    def test_five_and_one_hundred_slide_documents_have_contiguous_api_indexes(
        self,
    ) -> None:
        for count in (5, 100):
            details = {
                "presentation": {
                    "id": {"uuid": f"pres-{count}"},
                    "groups": [
                        {
                            "name": "Slides",
                            "slides": [
                                _slide(f"Slide {index}") for index in range(count)
                            ],
                        }
                    ],
                }
            }
            normalized = normalize_presentation(details)
            slides = [
                slide for group in normalized["groups"] for slide in group["slides"]
            ]
            self.assertEqual(normalized["slide_count"], count)
            self.assertEqual([slide["index"] for slide in slides], list(range(count)))

    def test_fallback_labels_and_active_lookup(self) -> None:
        normalized = normalize_presentation(
            {
                "presentation": {
                    "groups": [
                        {
                            "slides": [
                                {"text": "A short lyric"},
                                {"text": ""},
                            ]
                        }
                    ]
                }
            },
            "pres-fallback",
        )
        self.assertEqual(normalized["groups"][0]["slides"][0]["label"], "A short lyric")
        self.assertEqual(normalized["groups"][0]["slides"][1]["label"], "Slide 2")
        self.assertEqual(find_slide(normalized, 1)["label"], "Slide 2")
        self.assertEqual(find_slide_group(normalized, 1)["label"], "Group 1")

    def test_active_and_index_shapes(self) -> None:
        active = {"presentation_id": {"uuid": "pres-1", "name": "Song"}}
        index = {
            "presentation_index": {
                "presentation_id": {"uuid": "pres-1"},
                "index": 7,
            }
        }
        self.assertEqual(get_presentation_uuid(active), "pres-1")
        self.assertEqual(get_presentation_uuid(index), "pres-1")
        self.assertEqual(get_slide_index(index), 7)


if __name__ == "__main__":
    unittest.main()
