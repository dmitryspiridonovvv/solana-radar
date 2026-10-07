"""Minimal client for the Blur REST API (api.solami.dev/data/*)."""

import aiohttp

BLUR_REST_URL = "https://api.solami.dev"


class BlurApiError(RuntimeError):
    pass


class BlurRest:
    def __init__(self, api_key: str, base_url: str = BLUR_REST_URL, timeout: float = 10.0):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def _get(self, path: str, **params):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self.timeout, headers={"x-api-key": self.api_key})
        params = {"chain": "solana", **params}
        async with self._session.get(f"{self.base_url}{path}", params=params) as resp:
            body = await resp.json(content_type=None)
            if resp.status != 200:
                message = body.get("message") if isinstance(body, dict) else str(body)
                raise BlurApiError(f"{resp.status}: {message}")
            return body

    async def token_full(self, mint: str) -> dict:
        """Metadata, price, liquidity, pools and windowed stats in one call."""
        return await self._get("/data/token/full", address=mint)

    async def token_metadata(self, mint: str) -> dict:
        return await self._get("/data/token/metadata", address=mint)

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
