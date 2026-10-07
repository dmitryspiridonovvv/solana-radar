import pytest

from radar.alerts import Router, Subscription, format_event, stream_plan, usd

MINT = "94eHt3vvu8Fp2i2x2Ett2tvsKmyVABFNpEmZ7Gsjpump"


def swap(volume, mint=MINT, side="buy"):
    return {"type": "swap", "mint": mint, "side": side, "volume_usd": str(volume), "price_usd": "0.0000304", "dex": "pumpswap", "trader": "Trader1111111111111111111111111111", "signature": "sig1"}


@pytest.mark.parametrize(
    "value, text",
    [("2500000", "$2.50M"), ("15300", "$15.3K"), ("12.5", "$12.50"), ("0", "$0"), ("0.0000008672", "$0.0000008672"), (None, "$0")],
)
def test_usd_formatting_handles_blur_decimal_strings(value, text):
    assert usd(value) == text


def test_whale_threshold_is_per_chat():
    router = Router()
    small = Subscription(1, feeds={"whales"}, whale_usd=10_000)
    big = Subscription(2, feeds={"whales"}, whale_usd=100_000)
    assert [chat for chat, _ in router.route(swap(50_000), [small, big])] == [1]
    assert [chat for chat, _ in router.route(swap(150_000), [small, big])] == [1, 2]


def test_watched_token_gets_every_swap_even_without_whales_feed():
    router = Router()
    sub = Subscription(1, feeds=set(), watched={MINT})
    [(chat, text)] = router.route(swap(5), [sub])
    assert chat == 1 and "Watched token" in text


def test_paused_chats_get_nothing():
    router = Router()
    sub = Subscription(1, feeds={"surges"}, paused=True)
    assert router.route({"type": "surge", "mint": MINT, "multiple": "4"}, [sub]) == []


def test_metadata_event_names_later_alerts():
    router = Router()
    sub = Subscription(1, feeds={"surges"})
    assert router.route({"type": "metadata", "mint": MINT, "name": "Never Kill Yourself", "symbol": "NEVER"}, [sub]) == []
    [(_, text)] = router.route({"type": "surge", "mint": MINT, "multiple": "4.2", "window_secs": 300}, [sub])
    assert "$NEVER" in text and "x4.2" in text and "5 min" in text


def test_near_graduation_is_announced_once_per_mint_until_it_graduates():
    router = Router()
    sub = Subscription(1, feeds={"near", "graduations"})
    meme = {"type": "meme", "mint": MINT, "progress_pct": "91.5", "graduated": False, "launchpad": "pumpfun", "metadata": {"name": "N", "symbol": "NEV"}}
    assert len(router.route(meme, [sub])) == 1
    assert router.route({**meme, "progress_pct": "95"}, [sub]) == []
    assert router.route({**meme, "progress_pct": "80"}, [sub]) == []
    assert len(router.route({"type": "graduation", "mint": MINT, "launchpad": "pumpfun", "dex": "pumpswap"}, [sub])) == 1
    assert router.names[MINT]["symbol"] == "NEV"  # metadata embedded in the meme event is cached


def test_unknown_event_types_are_ignored():
    router = Router()
    sub = Subscription(1, feeds={"graduations", "surges", "whales", "near", "radar", "launches"})
    assert router.route({"type": "candle", "mint": MINT}, [sub]) == []


def test_html_in_token_names_is_escaped():
    text = format_event({"type": "token_create", "mint": MINT, "symbol": "<b>X</b>", "name": "a&b", "dex": "pumpfun"}, {}, "launches")
    assert "&lt;b&gt;X&lt;/b&gt;" in text and "a&amp;b" in text


def test_stream_plan_splits_filters_that_would_exclude_each_other():
    subs = [
        Subscription(1, feeds={"graduations", "surges", "whales"}, whale_usd=40_000),
        Subscription(2, feeds={"near", "whales"}, whale_usd=25_000, watched={MINT}),
        Subscription(3, feeds={"launches"}, paused=True),
    ]
    plan = stream_plan(subs)
    assert plan["signals"] == {"type": ["graduation", "surge"]}
    assert plan["near"]["min_progress"] == 90.0 and plan["near"]["type"] == ["meme"]
    assert plan["whales"] == {"type": ["swap"], "min_volume_usd": 25_000}
    assert plan["watch"] == {"type": ["swap"], "address": [MINT]}


def test_stream_plan_is_empty_without_active_subscribers():
    assert stream_plan([]) == {}
    assert stream_plan([Subscription(1, paused=True)]) == {}
