"""Pure tests for the shared thumbnail cache."""

from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import unittest

spec = spec_from_file_location(
    "propresenter_pure_thumbnail_cache",
    Path(__file__).parents[1]
    / "custom_components"
    / "propresenter"
    / "thumbnail_cache.py",
)
module = module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
ThumbnailCache = module.ThumbnailCache


class ThumbnailCacheTest(unittest.TestCase):
    def test_inflight_requests_are_deduplicated(self) -> None:
        async def run() -> None:
            cache = ThumbnailCache(max_items=128, max_bytes=1000)
            cache.set_current_identity("uuid", "revision")
            calls = 0

            async def fetch() -> bytes:
                nonlocal calls
                calls += 1
                await asyncio.sleep(0)
                return b"jpeg"

            key = ("uuid", "revision", 0, 400)
            first, second = await asyncio.gather(
                cache.get_or_fetch(key, fetch), cache.get_or_fetch(key, fetch)
            )
            self.assertEqual(first, b"jpeg")
            self.assertEqual(second, b"jpeg")
            self.assertEqual(calls, 1)
            self.assertEqual(cache.item_count, 1)

        asyncio.run(run())

    def test_item_and_byte_limits_are_enforced(self) -> None:
        async def run() -> None:
            cache = ThumbnailCache(max_items=2, max_bytes=8)
            cache.set_current_identity("uuid", "revision")

            async def fetch() -> bytes:
                return b"1234"

            for index in range(3):
                await cache.get_or_fetch(("uuid", "revision", index, 400), fetch)
            self.assertLessEqual(cache.item_count, 2)
            self.assertLessEqual(cache.total_bytes, 8)

        asyncio.run(run())

    def test_old_revision_cannot_be_populated_by_a_late_response(self) -> None:
        async def run() -> None:
            cache = ThumbnailCache(max_items=10, max_bytes=100)
            cache.set_current_identity("uuid", "old")
            release = asyncio.Event()

            async def fetch() -> bytes:
                await release.wait()
                return b"old bytes"

            task = asyncio.create_task(
                cache.get_or_fetch(("uuid", "old", 0, 400), fetch)
            )
            await asyncio.sleep(0)
            cache.set_current_identity("uuid", "new")
            release.set()
            self.assertEqual(await task, b"old bytes")
            self.assertEqual(cache.item_count, 0)

        asyncio.run(run())

    def test_fetches_are_bounded_to_four_per_entry(self) -> None:
        async def run() -> None:
            cache = ThumbnailCache(max_items=20, max_bytes=1000, max_concurrent=4)
            cache.set_current_identity("uuid", "revision")
            release = asyncio.Event()
            running = 0
            peak = 0

            async def fetch() -> bytes:
                nonlocal peak, running
                running += 1
                peak = max(peak, running)
                await release.wait()
                running -= 1
                return b"jpeg"

            tasks = [
                asyncio.create_task(
                    cache.get_or_fetch(("uuid", "revision", index, 400), fetch)
                )
                for index in range(8)
            ]
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertEqual(peak, 4)
            release.set()
            await asyncio.gather(*tasks)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
