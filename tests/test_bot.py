import datetime

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.base import BaseSession
from aiogram.methods import AnswerCallbackQuery, EditMessageReplyMarkup, SendMessage
from aiogram.types import CallbackQuery, Chat, Message, Update, User

from radar.bot import build_router
from radar.engine import RadarEngine
from radar.storage import SubscriptionStore

CHAT = 42
MINT = "94eHt3vvu8Fp2i2x2Ett2tvsKmyVABFNpEmZ7Gsjpump"


class FakeSession(BaseSession):
    def __init__(self):
        super().__init__()
        self.sent = []

    async def make_request(self, bot, method, timeout=None):
        if isinstance(method, SendMessage):
            self.sent.append(method)
            return Message(message_id=len(self.sent), date=datetime.datetime.now(), chat=Chat(id=method.chat_id, type="private"), text=method.text)
        if isinstance(method, (AnswerCallbackQuery, EditMessageReplyMarkup)):
            return True
        raise AssertionError(f"Unexpected API call: {type(method).__name__}")

    async def close(self):
        pass

    async def stream_content(self, *args, **kwargs):
        raise NotImplementedError


class FakeRest:
    async def token_full(self, mint):
        return {
            "mint": mint, "name": "Never Kill Yourself", "symbol": "NEVER", "price_usd": "0.00003036", "market_cap_usd": "29458.0",
            "ath_mcap_usd": "83076.8", "liquidity_usd": "4702.07", "holders": 310, "top10_pct": "25.1",
            "stats": {"3600": {"trades": 308, "buys": 308, "sells": 0, "traders": 77, "volume_usd": "11246.5"}},
            "screener": {"bonding_pct": "29.07", "is_graduated": False, "organic_score": "25.0", "launchpad": "pumpfun"},
            "dev": {"wallet": "DWHEZ8JnfGtud25Gjhn3MndpTMshNEd12WMu2ciCa7ri", "tokens_launched": 4},
            "pools": [{"dex": "pumpswap", "liquidity_usd": "4702.07", "tvl_usd": "4702.07", "lp_burn_pct": "100"}],
        }


class Harness:
    def __init__(self, tmp_path):
        self.session = FakeSession()
        self.bot = Bot("42:TEST", session=self.session, default=DefaultBotProperties(parse_mode="HTML"))
        self.store = SubscriptionStore(str(tmp_path / "radar.db"))
        self.engine = RadarEngine("k", self.store.all, lambda c, t: None, rest=FakeRest())
        self.syncs = 0

        async def fake_sync():
            self.syncs += 1

        self.engine.sync_streams = fake_sync
        self.dp = Dispatcher()
        self.dp.include_router(build_router(self.store, self.engine))
        self.n = 0

    def _message(self, text):
        self.n += 1
        user = User(id=CHAT, is_bot=False, first_name="T")
        return Message(message_id=self.n, date=datetime.datetime.now(), chat=Chat(id=CHAT, type="private"), from_user=user, text=text)

    async def send(self, text):
        await self.dp.feed_update(self.bot, Update(update_id=self.n + 1, message=self._message(text)))

    async def press(self, data):
        msg = self._message("keyboard")
        cb = CallbackQuery(id="1", from_user=msg.from_user, chat_instance="x", data=data, message=msg)
        await self.dp.feed_update(self.bot, Update(update_id=self.n + 1, callback_query=cb))

    @property
    def last(self):
        return self.session.sent[-1].text


@pytest.fixture()
def h(tmp_path):
    return Harness(tmp_path)


async def test_start_subscribes_with_defaults_and_syncs_streams(h):
    await h.send("/start")
    sub = h.store.get(CHAT)
    assert sub.feeds == {"graduations", "surges", "whales"} and h.syncs == 1
    assert "/feeds" in h.last


async def test_feed_toggle(h):
    await h.send("/start")
    await h.press("feed:near")
    await h.press("feed:surges")
    assert h.store.get(CHAT).feeds == {"graduations", "whales", "near"}


@pytest.mark.parametrize("arg, expected", [("25000", 25000.0), ("$100,000", 100000.0)])
async def test_whale_threshold(h, arg, expected):
    await h.send(f"/whale {arg}")
    assert h.store.get(CHAT).whale_usd == expected and "whales" in h.store.get(CHAT).feeds


@pytest.mark.parametrize("arg", ["", "abc", "500"])
async def test_whale_rejects_bad_values(h, arg):
    await h.send(f"/whale {arg}")
    assert h.store.get(CHAT).whale_usd == 50_000.0


async def test_watch_unwatch_and_validation(h):
    await h.send("/watch not-a-mint")
    assert "Usage" in h.last
    await h.send(f"/watch {MINT}")
    assert h.store.get(CHAT).watched == {MINT}
    await h.send("/watchlist")
    assert MINT in h.last
    await h.send(f"/unwatch {MINT}")
    assert h.store.get(CHAT).watched == set()


async def test_token_report(h):
    await h.send(f"/token {MINT}")
    text = h.last
    assert "$NEVER" in text and "Holders 310" in text and "bonding 29%" in text and "LP burned 100%" in text
    assert "⚠️" not in text  # 25% top-10, organic 25, LP burned: nothing to flag


def test_risk_flags_on_a_concentrated_token():
    from radar.reports import risk_flags

    data = {"top10_pct": "92.2", "screener": {"organic_score": "4", "dev_pct": "15"}, "dev": {"tokens_launched": 37}, "market_cap_usd": "426400", "liquidity_usd": "5000"}
    flags = risk_flags(data, {"lp_burn_pct": "0"})
    assert flags == ["top-10 holders own 92%", "low organic score (4)", "dev holds 15%", "dev launched 37 tokens", "only 0% LP burned", "thin liquidity for its market cap"]


async def test_pause_resume(h):
    await h.send("/pause")
    assert h.store.get(CHAT).paused
    await h.send("/resume")
    assert not h.store.get(CHAT).paused


async def test_stats_renders_without_streams(h):
    await h.send("/stats")
    assert "Stream health" in h.last and "no active streams" in h.last
