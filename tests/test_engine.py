import asyncio

from radar import engine as engine_module
from radar.alerts import Subscription
from radar.engine import MAX_ALERTS_PER_MINUTE, RadarEngine
from tests.fake_blur import FakeBlur
from tests.test_blur_stream import wait_for

MINT = "94eHt3vvu8Fp2i2x2Ett2tvsKmyVABFNpEmZ7Gsjpump"


class FakeRest:
    def __init__(self):
        self.calls = 0

    async def token_metadata(self, mint):
        self.calls += 1
        return {"mint": mint, "name": "Never Kill Yourself", "symbol": "NEVER"}


async def test_engine_opens_matching_streams_and_delivers_alerts(monkeypatch):
    monkeypatch.setattr(engine_module, "NAME_WAIT_SECS", 0.05)
    subs = [Subscription(1, feeds={"surges", "whales"}, whale_usd=20_000)]
    sent = []

    async def send(chat_id, text):
        sent.append((chat_id, text))

    async with FakeBlur() as blur:
        engine = RadarEngine("good-key", lambda: subs, send, rest=FakeRest(), base_url=blur.url)
        await engine.sync_streams()
        await blur.wait_connections(2)
        assert {q["type"] for q, _ in blur.connections} == {"surge", "swap"}

        await blur.send_to("swap", {"type": "swap", "mint": MINT, "side": "buy", "volume_usd": "25000"})
        await wait_for(lambda: sent)
        assert sent[0][0] == 1 and "$NEVER" in sent[0][1]  # name filled from REST after the wait

        subs[0].feeds = {"surges"}
        await engine.sync_streams()
        assert set(engine.streams) == {"signals"}
        await engine.stop()


async def test_metadata_on_the_stream_avoids_a_rest_call(monkeypatch):
    monkeypatch.setattr(engine_module, "NAME_WAIT_SECS", 1.0)
    rest = FakeRest()
    sent = []

    async def send(chat_id, text):
        sent.append(text)

    engine = RadarEngine("k", lambda: [Subscription(1, feeds={"surges"})], send, rest=rest)
    await engine.handle_event("signals", {"type": "surge", "mint": MINT, "multiple": "5"})
    await engine.handle_event("signals", {"type": "metadata", "mint": MINT, "name": "Stream Name", "symbol": "STRM"})
    await wait_for(lambda: sent)
    assert "$STRM" in sent[0] and rest.calls == 0
    await engine.stop()


async def test_per_chat_rate_limit_drops_floods():
    sent = []

    async def send(chat_id, text):
        sent.append(chat_id)

    sub = Subscription(1, feeds={"graduations"})
    engine = RadarEngine("k", lambda: [sub], send)
    engine.router.names[MINT] = {"name": "N", "symbol": "S"}
    for _ in range(MAX_ALERTS_PER_MINUTE["signal"] + 5):
        await engine.handle_event("signals", {"type": "graduation", "mint": MINT})
    assert len(sent) == MAX_ALERTS_PER_MINUTE["signal"]
    assert engine.metrics.alerts_dropped == 5


async def test_whale_flood_cannot_crowd_out_graduations():
    sent = []

    async def send(chat_id, text):
        sent.append(text)

    sub = Subscription(1, feeds={"graduations", "whales"}, whale_usd=5_000)
    engine = RadarEngine("k", lambda: [sub], send)
    engine.router.names[MINT] = {"name": "N", "symbol": "S"}
    for _ in range(100):
        await engine.handle_event("whales", {"type": "swap", "mint": MINT, "side": "sell", "volume_usd": "9000"})
    await engine.handle_event("signals", {"type": "graduation", "mint": MINT})
    assert any("Graduated" in t for t in sent)
    assert sum("Whale swap" in t for t in sent) == MAX_ALERTS_PER_MINUTE["swap"]


async def test_failed_delivery_is_logged_not_raised():
    async def send(chat_id, text):
        raise RuntimeError("bot was blocked by the user")

    engine = RadarEngine("k", lambda: [Subscription(1, feeds={"graduations"})], send)
    engine.router.names[MINT] = {"name": "N", "symbol": "S"}
    await engine.handle_event("signals", {"type": "graduation", "mint": MINT})
    assert engine.metrics.alerts_sent == 0


async def test_fatal_key_error_is_reported():
    async with FakeBlur() as blur:
        engine = RadarEngine("bad-key", lambda: [Subscription(1)], lambda c, t: asyncio.sleep(0), base_url=blur.url)
        await engine.sync_streams()
        await wait_for(lambda: len(engine.fatal_errors()) == len(engine.streams))
        assert all("DataApi" in e for e in engine.fatal_errors().values())
        await engine.stop()
