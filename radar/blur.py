"""Blur WebSocket client: one connection per filter, automatic reconnect, live filter updates."""

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from urllib.parse import urlencode

import websockets

log = logging.getLogger(__name__)

BLUR_WS_URL = "wss://ws.solami.dev/data/subscribe"

# Close codes the server uses for problems a reconnect cannot fix.
FATAL_CLOSE_CODES = {4001: "invalid API key or missing DataApi permission", 4002: "out of streaming bandwidth"}

EventHandler = Callable[[str, dict], Awaitable[None]]

# Connect-time query params use singular names; the live filter frame uses plural ones.
LIVE_FILTER_KEYS = {"type": "types", "address": "mints", "pool": "pools", "trader": "traders", "dex": "dexes"}
LIVE_FILTER_SCALARS = {"side", "min_volume_usd", "min_base", "min_quote", "metadata"}


def live_filter_frame(params: dict) -> str | None:
    """JSON frame that replaces the filter without reconnecting, or None if a param has no live form."""
    out = {}
    for key, value in params.items():
        if key in LIVE_FILTER_KEYS:
            values = value if isinstance(value, (list, tuple, set)) else [value]
            out[LIVE_FILTER_KEYS[key]] = sorted(str(v) for v in values)
        elif key in LIVE_FILTER_SCALARS:
            out[key] = float(value) if key == "min_volume_usd" else value
        else:
            return None
    return json.dumps({"filter": out})


def build_url(api_key: str, params: dict, base_url: str = BLUR_WS_URL) -> str:
    """Connect-time filter: lists become comma-separated values, empty values are dropped."""
    query = {"chain": "solana", "api_key": api_key}
    for key, value in params.items():
        if value is None or value == [] or value == "":
            continue
        if isinstance(value, (list, tuple, set)):
            value = ",".join(sorted(str(v) for v in value))
        elif isinstance(value, bool):
            value = "true" if value else "false"
        query[key] = value
    return f"{base_url}?{urlencode(query, safe=',')}"


class BlurStream:
    """A single filtered Blur subscription that keeps itself connected.

    Every decoded event is passed to `on_event(stream_name, event)`. The filter can be
    replaced while running with `update_filter`, which reconnects only if needed.
    """

    def __init__(
        self,
        name: str,
        api_key: str,
        params: dict,
        on_event: EventHandler,
        metrics=None,
        base_url: str = BLUR_WS_URL,
        max_backoff: float = 30.0,
    ):
        self.name = name
        self.api_key = api_key
        self.params = dict(params)
        self.on_event = on_event
        self.metrics = metrics
        self.base_url = base_url
        self.max_backoff = max_backoff
        self.connected = asyncio.Event()
        self.fatal_error: str | None = None
        self._ws = None
        self._reconnect = asyncio.Event()
        self._stopped = False

    @property
    def url(self) -> str:
        return build_url(self.api_key, self.params, self.base_url)

    async def update_filter(self, params: dict) -> None:
        """Swap the filter on the open socket when possible, otherwise reconnect with the new query."""
        if params == self.params:
            return
        self.params = dict(params)
        frame = live_filter_frame(self.params)
        if self._ws is not None and frame is not None:
            try:
                await self._ws.send(frame)
                log.info("[%s] filter updated live", self.name)
                return
            except websockets.ConnectionClosed:
                pass  # fall through: the reconnect below picks up the new params
        self._reconnect.set()
        if self._ws is not None:
            await self._ws.close()

    async def stop(self) -> None:
        self._stopped = True
        self._reconnect.set()
        if self._ws is not None:
            await self._ws.close()

    async def run(self) -> None:
        backoff = 1.0
        while not self._stopped:
            self._reconnect.clear()
            try:
                async with websockets.connect(self.url, open_timeout=15, ping_interval=20, max_size=2**22) as ws:
                    self._ws = ws
                    self.connected.set()
                    backoff = 1.0
                    log.info("[%s] connected", self.name)
                    async for raw in ws:
                        await self._dispatch(raw)
                    code = ws.close_code
            except websockets.InvalidStatus as exc:
                status = exc.response.status_code
                if status in (401, 403):
                    self.fatal_error = f"HTTP {status}: key rejected (needs the DataApi permission)"
                    log.error("[%s] %s", self.name, self.fatal_error)
                    return
                code = None
                log.warning("[%s] handshake failed: HTTP %s", self.name, status)
            except websockets.ConnectionClosed as exc:
                code = exc.rcvd.code if exc.rcvd else None
                log.warning("[%s] connection lost: %s", self.name, exc)
            except (OSError, asyncio.TimeoutError) as exc:
                code = None
                log.warning("[%s] connection failed: %s", self.name, exc)
            finally:
                self._ws = None
                self.connected.clear()

            if code in FATAL_CLOSE_CODES:
                self.fatal_error = f"close {code}: {FATAL_CLOSE_CODES[code]}"
                log.error("[%s] %s", self.name, self.fatal_error)
                return
            if self._stopped:
                return
            if self._reconnect.is_set():
                continue  # deliberate reconnect after a filter change
            if self.metrics:
                self.metrics.on_reconnect(self.name)
            delay = backoff + random.uniform(0, backoff / 2)
            log.info("[%s] reconnecting in %.1fs", self.name, delay)
            try:
                await asyncio.wait_for(self._reconnect.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, self.max_backoff)

    async def _dispatch(self, raw: str | bytes) -> None:
        try:
            event = json.loads(raw)
        except (TypeError, ValueError):
            log.debug("[%s] skipped non-JSON frame", self.name)
            return
        if not isinstance(event, dict) or "type" not in event:
            return
        if self.metrics:
            self.metrics.on_event(self.name, event, len(raw))
        try:
            await self.on_event(self.name, event)
        except Exception:  # one bad handler call must not kill the stream
            log.exception("[%s] handler failed for %s event", self.name, event.get("type"))
