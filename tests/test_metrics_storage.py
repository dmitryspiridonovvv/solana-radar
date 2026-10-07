from radar.alerts import Subscription
from radar.metrics import Metrics
from radar.storage import SubscriptionStore


class Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def test_metrics_window_counts_and_volumes():
    clock = Clock()
    m = Metrics(clock=clock)
    m.on_event("signals", {"type": "token_create", "block_time": clock.now - 1}, 100)
    m.on_event("signals", {"type": "graduation"}, 50)
    m.on_event("whales", {"type": "swap", "side": "buy", "volume_usd": "60000.5", "block_time": clock.now - 2}, 200)
    m.on_event("whales", {"type": "swap", "side": "sell", "volume_usd": "40000", "block_time": clock.now - 4}, 200)
    m.on_event("signals", {"type": "surge", "mint": "M1", "multiple": "7.5"}, 80)
    m.on_reconnect("whales")
    snap = m.snapshot()
    assert (snap["launches_1h"], snap["graduations_1h"], snap["surges_1h"], snap["whale_swaps_1h"]) == (1, 1, 1, 2)
    assert snap["whale_buy_usd_1h"] == 60000.5 and snap["whale_sell_usd_1h"] == 40000
    assert snap["lag_p50_secs"] == 2 and snap["reconnects"] == 1 and snap["events_per_min"] == 5
    assert snap["top_surges"] == [{"mint": "M1", "multiple": 7.5}]


def test_outlier_swaps_are_counted_but_not_added_to_volume():
    m = Metrics(clock=Clock())
    m.on_event("whales", {"type": "swap", "side": "sell", "volume_usd": "584806", "candle_ok": False})
    m.on_event("whales", {"type": "swap", "side": "sell", "volume_usd": "9000", "candle_ok": True})
    snap = m.snapshot()
    assert snap["whale_sell_usd_1h"] == 9000 and snap["outliers_1h"] == 1 and snap["whale_swaps_1h"] == 1


def test_metrics_drop_events_older_than_an_hour():
    clock = Clock()
    m = Metrics(clock=clock)
    m.on_event("signals", {"type": "graduation"})
    clock.now += 3601
    snap = m.snapshot()
    assert snap["graduations_1h"] == 0 and snap["events_total"] == 1 and snap["lag_p50_secs"] is None


def test_metrics_tolerate_null_and_garbage_numbers():
    m = Metrics(clock=Clock())
    m.on_event("whales", {"type": "swap", "side": "buy", "volume_usd": None})
    m.on_event("whales", {"type": "swap", "side": "buy", "volume_usd": "nan?"})
    assert m.snapshot()["whale_buy_usd_1h"] == 0


def test_store_roundtrip(tmp_path):
    store = SubscriptionStore(str(tmp_path / "r.db"))
    sub = store.get_or_create(42)
    sub.feeds = {"near"}
    sub.watched = {"MintA", "MintB"}
    sub.whale_usd = 12_345.0
    sub.paused = True
    store.save(sub)
    again = SubscriptionStore(str(tmp_path / "r.db")).get(42)
    assert again == Subscription(42, feeds={"near"}, whale_usd=12_345.0, watched={"MintA", "MintB"}, paused=True)
    store.delete(42)
    assert store.get(42) is None and store.all() == []
