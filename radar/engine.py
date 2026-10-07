"""Glue between Blur streams and alert delivery: keeps the stream set matched to subscriptions."""

import asyncio
import logging
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable

from .alerts import Router, Subscription, stream_plan
from .blur import BLUR_WS_URL, BlurStream
from .metrics import Metrics

log = logging.getLogger(__name__)

MAX_ALERTS_PER_MINUTE = 20  # per chat; Telegram itself allows roughly one message per second
NAME_WAIT_SECS = 1.5  # metadata arrives out of band, usually right after the first event for a mint

Sender = Callable[[int, str], Awaitable[None]]


class RadarEngine:
    def __init__(
        self,
        api_key: str,
        get_subscriptions: Callable[[], list[Subscription]],
        send: Sender,
        rest=None,
        metrics: Metrics | None = None,
        base_url: str = BLUR_WS_URL,
        clock=time.monotonic,
    ):
        self.api_key = api_key
        self.get_subscriptions = get_subscriptions
        self.send = send
        self.rest = rest
        self.metrics = metrics or Metrics()
        self.base_url = base_url
        self.clock = clock
        self.router = Router()
        self.streams: dict[str, BlurStream] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._sent = defaultdict(deque)
        self._pending: set[asyncio.Task] = set()

    async def sync_streams(self) -> None:
        """Open, retune or close connections so they match the current subscriptions."""
        plan = stream_plan(self.get_subscriptions())
        for name in list(self.streams):
            if name not in plan:
                await self.streams.pop(name).stop()
                self._tasks.pop(name).cancel()
        for name, params in plan.items():
            if name in self.streams:
                await self.streams[name].update_filter(params)
            else:
                stream = BlurStream(name, self.api_key, params, self.handle_event, self.metrics, self.base_url)
                self.streams[name] = stream
                self._tasks[name] = asyncio.create_task(stream.run(), name=f"blur-{name}")

    async def handle_event(self, stream_name: str, event: dict) -> None:
        mint = event.get("mint")
        if event.get("type") in ("surge", "radar", "graduation", "swap") and mint and mint not in self.router.names:
            # Don't hold up the socket while the token name resolves; route this one event later.
            task = asyncio.create_task(self._route_after_name(event))
            self._pending.add(task)
            task.add_done_callback(self._pending.discard)
            return
        await self._route(event)

    async def _route_after_name(self, event: dict) -> None:
        await self._resolve_name(event.get("mint"))
        await self._route(event)

    async def _route(self, event: dict) -> None:
        for chat_id, text in self.router.route(event, self.get_subscriptions()):
            await self._deliver(chat_id, text)

    async def _resolve_name(self, mint: str | None) -> None:
        if not mint or mint in self.router.names:
            return
        deadline = self.clock() + NAME_WAIT_SECS
        while self.clock() < deadline:
            await asyncio.sleep(0.1)
            if mint in self.router.names:
                return
        if self.rest is not None:
            try:
                meta = await self.rest.token_metadata(mint)
                if isinstance(meta, list):
                    meta = meta[0] if meta else {}
                self.router.remember_metadata({"mint": mint, **meta})
            except Exception as exc:
                log.debug("metadata lookup failed for %s: %s", mint, exc)

    async def _deliver(self, chat_id: int, text: str) -> None:
        now = self.clock()
        sent = self._sent[chat_id]
        while sent and sent[0] < now - 60:
            sent.popleft()
        if len(sent) >= MAX_ALERTS_PER_MINUTE:
            self.metrics.alerts_dropped += 1
            return
        sent.append(now)
        try:
            await self.send(chat_id, text)
            self.metrics.alerts_sent += 1
        except Exception as exc:
            log.warning("delivery to %s failed: %s", chat_id, exc)

    def fatal_errors(self) -> dict[str, str]:
        return {name: s.fatal_error for name, s in self.streams.items() if s.fatal_error}

    async def stop(self) -> None:
        for stream in self.streams.values():
            await stream.stop()
        tasks = [*self._tasks.values(), *self._pending]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self.streams.clear()
        self._tasks.clear()
