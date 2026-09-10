"""Tests for the strict slide-trigger HTTP path."""

from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import unittest

try:
    import aiohttp
    import async_timeout  # noqa: F401
except ModuleNotFoundError as err:  # pragma: no cover - dependency supplied by CI
    raise unittest.SkipTest(f"API dependency unavailable: {err}") from err

spec = spec_from_file_location(
    "propresenter_pure_api",
    Path(__file__).parents[1] / "custom_components" / "propresenter" / "api.py",
)
module = module_from_spec(spec)
spec.loader.exec_module(module)
ProPresenterAPI = module.ProPresenterAPI
ProPresenterNotFoundError = module.ProPresenterNotFoundError
ProPresenterRequestError = module.ProPresenterRequestError


class _Response:
    def __init__(self, status: int, *, content_length: int | None = 0) -> None:
        self.status = status
        self.content_length = content_length
        self.headers = {"content-type": "application/json"}

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise aiohttp.ClientResponseError(None, (), status=self.status)

    async def json(self) -> dict:
        return {"ok": True}


class _ResponseContext:
    def __init__(self, response: _Response) -> None:
        self.response = response

    async def __aenter__(self) -> _Response:
        return self.response

    async def __aexit__(self, *_args) -> None:
        return None


class _Session:
    closed = False

    def __init__(self, response: _Response) -> None:
        self.response = response

    def request(self, *_args, **_kwargs) -> _ResponseContext:
        return _ResponseContext(self.response)


class StrictRequestTest(unittest.TestCase):
    def test_404_is_not_collapsed_into_success(self) -> None:
        async def run() -> None:
            api = ProPresenterAPI("127.0.0.1")
            api._session = _Session(_Response(404))
            with self.assertRaises(ProPresenterNotFoundError):
                await api._request_strict("GET", "/v1/presentation/active/2/trigger")

        asyncio.run(run())

    def test_empty_success_is_successful(self) -> None:
        async def run() -> None:
            api = ProPresenterAPI("127.0.0.1")
            api._session = _Session(_Response(204))
            self.assertIsNone(
                await api._request_strict("GET", "/v1/presentation/active/2/trigger")
            )

        asyncio.run(run())

    def test_other_http_errors_are_distinct(self) -> None:
        async def run() -> None:
            api = ProPresenterAPI("127.0.0.1")
            api._session = _Session(_Response(500))
            with self.assertRaises(ProPresenterRequestError):
                await api._request_strict("GET", "/v1/presentation/active/2/trigger")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
