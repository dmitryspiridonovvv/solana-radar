"""Rolling one-hour metrics over the live stream: market activity and stream health."""

import time
from collections import Counter, deque
from statistics import median

WINDOW_SECS = 3600


def num(value, default: float = 0.0) -> float:
    """Blur sends fractional values as decimal strings; non-finite values arrive as null."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class Metrics:
    def __init__(self, clock=time.time):
        self.clock = clock
        self.started = clock()
        self.events_total = 0
        self.bytes_total = 0
        self.reconnects = Counter()
        self.alerts_sent = 0
        self.alerts_dropped = 0
        self._events = deque()  # (ts, type)
        self._swaps = deque()  # (ts, volume_usd, side, mint)
        self._lags = deque()  # (ts, seconds behind block time)
        self._surges = deque()  # (ts, mint, multiple)
        self._outliers = deque()  # (ts,) swaps flagged candle_ok=false

    def _trim(self, now: float) -> None:
        cutoff = now - WINDOW_SECS
        for q in (self._events, self._swaps, self._lags, self._surges, self._outliers):
            while q and q[0][0] < cutoff:
                q.popleft()

    def on_event(self, stream: str, event: dict, size: int = 0) -> None:
        now = self.clock()
        kind = event.get("type", "?")
        self.events_total += 1
        self.bytes_total += size
        self._events.append((now, kind))
        block_time = event.get("block_time")
        if isinstance(block_time, (int, float)) and block_time > 0:
            self._lags.append((now, max(0.0, now - block_time)))
        if kind == "swap":
            if event.get("candle_ok") is False:
                self._outliers.append((now,))
            else:
                self._swaps.append((now, num(event.get("volume_usd")), event.get("side"), event.get("mint")))
        elif kind in ("surge", "radar"):
            self._surges.append((now, event.get("mint"), num(event.get("multiple"))))
        self._trim(now)

    def on_reconnect(self, stream: str) -> None:
        self.reconnects[stream] += 1

    def snapshot(self) -> dict:
        now = self.clock()
        self._trim(now)
        by_type = Counter(kind for _, kind in self._events)
        buy_usd = sum(v for _, v, side, _ in self._swaps if side == "buy")
        sell_usd = sum(v for _, v, side, _ in self._swaps if side == "sell")
        lags = sorted(lag for _, lag in self._lags)
        top_surges = sorted(self._surges, key=lambda s: s[2], reverse=True)[:3]
        recent = [ts for ts, _ in self._events if ts >= now - 60]
        return {
            "uptime_secs": int(now - self.started),
            "events_total": self.events_total,
            "events_per_min": len(recent),
            "mb_received": round(self.bytes_total / 1_000_000, 2),
            "by_type": dict(by_type),
            "launches_1h": by_type.get("token_create", 0),
            "graduations_1h": by_type.get("graduation", 0),
            "surges_1h": by_type.get("surge", 0) + by_type.get("radar", 0),
            "whale_swaps_1h": len(self._swaps),
            "whale_buy_usd_1h": round(buy_usd, 2),
            "whale_sell_usd_1h": round(sell_usd, 2),
            "outliers_1h": len(self._outliers),
            "lag_p50_secs": round(median(lags), 2) if lags else None,
            "lag_p95_secs": round(lags[int(0.95 * (len(lags) - 1))], 2) if lags else None,
            "reconnects": sum(self.reconnects.values()),
            "alerts_sent": self.alerts_sent,
            "alerts_dropped": self.alerts_dropped,
            "top_surges": [{"mint": m, "multiple": round(x, 1)} for _, m, x in top_surges],
        }
