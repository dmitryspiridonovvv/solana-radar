import asyncio
import json

import pytest

from radar.blur import BlurStream, build_url, live_filter_frame
from radar.metrics import Metrics
from tests.fake_blur import FakeBlur


async def wait_for(predicate, timeout=5):
    async def poll():
        while not predicate():
            await asyncio.sleep(0.02)
    await asyncio.wait_for(poll(), timeout)


def test_build_url_joins_lists_and_drops_empty_values():
    url = build_url("k", {"type": ["swap", "graduation"], "min_volume_usd": 25000, "address": [], "metadata": False}, "wss://x/data/subscribe")
    assert url == "wss://x/data/subscribe?chain=solana&api_key=k&type=graduation,swap&min_volume_usd=25000&metadata=false"


def test_live_filter_frame_uses_plural_keys():
    frame = json.loads(live_filter_frame({"type": ["swap"], "address": ["M2", "M1"], "min_volume_usd": 500}))
    assert frame == {"filter": {"types": ["swap"], "mints": ["M1", "M2"], "min_volume_usd": 500.0}}
    assert live_filter_frame({"min_progress": 90}) is None  # no live form documented, so reconnect instead


async def test_stream_delivers_events_and_skips_garbage():
    got = []

    async def on_event(name, event):
        got.append((name, event["type"]))

    metrics = Metrics()
    async with FakeBlur() as blur:
        stream = BlurStream("signals", "good-key", {"type": ["surge"]}, on_event, metrics, base_url=blur.url)
        task = asyncio.create_task(stream.run())
        await blur.wait_connections(1)
        assert blur.connections[0][0]["type"] == "surge"
        await blur.broadcast({"type": "surge", "mint": "M"}, {"no_type": 1}, raw="not json")
        await wait_for(lambda: got)
        await stream.stop()
        await task
    assert got == [("signals", "surge")]
    assert metrics.events_total == 1


async def test_stream_reconnects_after_server_drop():
    got = []

    async def on_event(name, event):
        got.append(event["mint"])

    metrics = Metrics()
    async with FakeBlur() as blur:
        stream = BlurStream("whales", "good-key", {"type": ["swap"]}, on_event, metrics, base_url=blur.url, max_backoff=0.2)
        task = asyncio.create_task(stream.run())
        await blur.wait_connections(1)
        await blur.drop_all()
        await wait_for(lambda: len(blur.connections) == 2)
        await blur.wait_connections(1)
        await blur.broadcast({"type": "swap", "mint": "after-reconnect"})
        await wait_for(lambda: got)
        await stream.stop()
        await task
    assert got == ["after-reconnect"] and metrics.reconnects["whales"] == 1


async def test_rejected_key_stops_without_retrying():
    async with FakeBlur() as blur:
        stream = BlurStream("signals", "bad-key", {"type": ["surge"]}, None, base_url=blur.url)
        await asyncio.wait_for(stream.run(), 5)
    assert "DataApi" in stream.fatal_error and blur.connections == []


async def test_out_of_bandwidth_close_is_fatal():
    async with FakeBlur() as blur:
        stream = BlurStream("signals", "good-key", {"type": ["surge"]}, None, base_url=blur.url, max_backoff=0.1)
        task = asyncio.create_task(stream.run())
        await blur.wait_connections(1)
        await blur.drop_all(code=4002)
        await asyncio.wait_for(task, 5)
    assert "bandwidth" in stream.fatal_error and len(blur.connections) == 1


async def test_filter_update_is_sent_live_when_possible_and_reconnects_otherwise():
    async def on_event(name, event):
        pass

    async with FakeBlur() as blur:
        stream = BlurStream("watch", "good-key", {"type": ["swap"], "address": ["M1"]}, on_event, base_url=blur.url)
        task = asyncio.create_task(stream.run())
        await blur.wait_connections(1)
        await stream.update_filter({"type": ["swap"], "address": ["M1", "M2"]})
        await wait_for(lambda: blur.received)
        assert json.loads(blur.received[0])["filter"]["mints"] == ["M1", "M2"]
        assert len(blur.connections) == 1

        await stream.update_filter({"type": ["meme"], "min_progress": 90})
        await wait_for(lambda: len(blur.connections) == 2)
        assert blur.connections[1][0]["min_progress"] == "90"
        await stream.stop()
        await task


async def test_handler_errors_do_not_kill_the_stream():
    calls = []

    async def on_event(name, event):
        calls.append(event["n"])
        if event["n"] == 1:
            raise RuntimeError("boom")

    async with FakeBlur() as blur:
        stream = BlurStream("signals", "good-key", {"type": ["surge"]}, on_event, base_url=blur.url)
        task = asyncio.create_task(stream.run())
        await blur.wait_connections(1)
        await blur.broadcast({"type": "surge", "n": 1}, {"type": "surge", "n": 2})
        await wait_for(lambda: len(calls) == 2)
        await stream.stop()
        await task
    assert calls == [1, 2]
