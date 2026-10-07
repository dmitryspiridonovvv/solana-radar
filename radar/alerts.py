"""Turning decoded Blur events into Telegram alerts: who gets what, and how it reads."""

from dataclasses import dataclass, field
from html import escape

from .metrics import num

FEEDS = {
    "graduations": "Launchpad tokens that just got a real AMM pool",
    "near": "Bonding curve at 90%+, about to graduate",
    "surges": "5-minute volume breakouts (3x+ over the last hour)",
    "radar": "30-minute steady climbs (1.8x+ over 6 hours)",
    "whales": "Single swaps above your USD threshold",
    "launches": "Every new token mint (very noisy)",
}
DEFAULT_FEEDS = frozenset({"graduations", "surges", "whales"})
DEFAULT_WHALE_USD = 50_000.0
MIN_WHALE_USD = 1_000.0
NEAR_GRADUATION_PCT = 90.0
MAX_WATCHED_MINTS = 20


@dataclass
class Subscription:
    chat_id: int
    feeds: set = field(default_factory=lambda: set(DEFAULT_FEEDS))
    whale_usd: float = DEFAULT_WHALE_USD
    watched: set = field(default_factory=set)
    paused: bool = False


def short(address: str | None, size: int = 4) -> str:
    if not address:
        return "?"
    return address if len(address) <= size * 2 + 1 else f"{address[:size]}…{address[-size:]}"


def usd(value) -> str:
    amount = num(value)
    if amount >= 1_000_000:
        return f"${amount / 1_000_000:.2f}M"
    if amount >= 1_000:
        return f"${amount / 1_000:.1f}K"
    if amount >= 1:
        return f"${amount:,.2f}"
    if amount == 0:
        return "$0"
    return f"${amount:.10f}".rstrip("0")


def token_label(mint: str, names: dict) -> str:
    meta = names.get(mint) or {}
    symbol, name = meta.get("symbol"), meta.get("name")
    if symbol:
        return f"<b>${escape(symbol)}</b>" + (f" ({escape(name)})" if name else "")
    return f"<code>{short(mint)}</code>"


def links(mint: str, signature: str | None = None) -> str:
    parts = [
        f'<a href="https://dexscreener.com/solana/{mint}">DexScreener</a>',
        f'<a href="https://solscan.io/token/{mint}">Solscan</a>',
    ]
    if signature:
        parts.append(f'<a href="https://solscan.io/tx/{signature}">tx</a>')
    return " · ".join(parts)


def format_event(event: dict, names: dict, reason: str) -> str:
    kind = event.get("type")
    mint = event.get("mint", "")
    label = token_label(mint, names)
    if kind == "swap":
        side = event.get("side", "?")
        icon = "🟢" if side == "buy" else "🔴"
        title = "Watched token" if reason == "watch" else "Whale swap"
        return (
            f"{icon} <b>{title}</b>: {side.upper()} {usd(event.get('volume_usd'))} of {label}\n"
            f"Price {usd(event.get('price_usd'))} · {escape(event.get('dex', '?'))} · impact {num(event.get('price_impact_pct')):.2f}%\n"
            f"Trader <code>{short(event.get('trader'))}</code>\n{links(mint, event.get('signature'))}"
        )
    if kind in ("surge", "radar"):
        window = int(event.get("window_secs", 300)) // 60
        return (
            f"🚀 <b>{'Surge' if kind == 'surge' else 'Radar'}</b>: {label} volume x{num(event.get('multiple')):.1f} in {window} min\n"
            f"{usd(event.get('volume_window_usd'))} vs baseline {usd(event.get('baseline_usd'))} · "
            f"{event.get('trades', 0)} trades · ~{event.get('traders_est', 0)} traders\n"
            f"Mcap {usd(event.get('mcap_at_trigger'))} · price {usd(event.get('price_at_trigger'))}\n{links(mint)}"
        )
    if kind == "graduation":
        return (
            f"🎓 <b>Graduated</b>: {label} left {escape(str(event.get('launchpad', 'launchpad')))} "
            f"for {escape(str(event.get('dex', 'an AMM')))}\nPool <code>{short(event.get('pool'))}</code>\n"
            f"{links(mint, event.get('signature'))}"
        )
    if kind == "meme":
        window = (event.get("windows") or {}).get("300") or {}
        return (
            f"⏳ <b>About to graduate</b>: {label} bonding curve at {num(event.get('progress_pct')):.0f}% "
            f"on {escape(str(event.get('launchpad', '?')))}\n"
            f"Price {usd(event.get('price_usd'))} · 5m volume {usd(window.get('volume_usd'))} "
            f"({window.get('buys', 0)} buys / {window.get('sells', 0)} sells)\n{links(mint)}"
        )
    if kind == "token_create":
        return (
            f"✨ <b>New token</b>: <b>${escape(event.get('symbol') or '?')}</b> {escape(event.get('name') or '')} "
            f"on {escape(event.get('dex', '?'))}\nCreator <code>{short(event.get('creator'))}</code>\n"
            f"{links(mint, event.get('signature'))}"
        )
    return f"{escape(str(kind))} event for {label}"


class Router:
    """Decides which chats an event goes to, and remembers what it has already announced."""

    def __init__(self):
        self.names: dict[str, dict] = {}
        self._near_announced: set[str] = set()

    def remember_metadata(self, event: dict) -> None:
        mint = event.get("mint")
        if mint:
            self.names[mint] = {"name": event.get("name"), "symbol": event.get("symbol")}
            if len(self.names) > 50_000:  # bounded cache: drop the oldest half
                for key in list(self.names)[:25_000]:
                    del self.names[key]

    def route(self, event: dict, subs: list[Subscription]) -> list[tuple[int, str]]:
        kind = event.get("type")
        if kind == "metadata":
            self.remember_metadata(event)
            return []
        if kind == "token_create":
            self.remember_metadata(event)
        mint = event.get("mint")
        if kind == "meme":
            if event.get("graduated") or num(event.get("progress_pct")) < NEAR_GRADUATION_PCT or mint in self._near_announced:
                return []
            self._near_announced.add(mint)
            if isinstance(event.get("metadata"), dict):
                self.remember_metadata({"mint": mint, **event["metadata"]})
        if kind == "graduation":
            self._near_announced.discard(mint)

        out = []
        for sub in subs:
            if sub.paused:
                continue
            reason = self._reason(event, sub)
            if reason:
                out.append((sub.chat_id, format_event(event, self.names, reason)))
        return out

    @staticmethod
    def _reason(event: dict, sub: Subscription) -> str | None:
        kind = event.get("type")
        if kind == "swap":
            if event.get("mint") in sub.watched:
                return "watch"
            if "whales" in sub.feeds and num(event.get("volume_usd")) >= sub.whale_usd:
                return "whales"
            return None
        feed = {"graduation": "graduations", "meme": "near", "surge": "surges", "radar": "radar", "token_create": "launches"}.get(kind)
        return feed if feed in sub.feeds else None


def stream_plan(subs: list[Subscription]) -> dict[str, dict]:
    """Blur filters for the union of everyone's subscriptions.

    Field filters such as min_volume_usd or min_progress silently drop every event type they
    do not apply to, so each kind of interest gets its own connection.
    """
    active = [s for s in subs if not s.paused]
    feeds = set().union(*(s.feeds for s in active)) if active else set()
    plan = {}
    signal_types = sorted(
        t for f, t in (("graduations", "graduation"), ("surges", "surge"), ("radar", "radar"), ("launches", "token_create")) if f in feeds
    )
    if signal_types:
        plan["signals"] = {"type": signal_types}
    if "near" in feeds:
        plan["near"] = {"type": ["meme"], "min_progress": NEAR_GRADUATION_PCT, "metadata": False}
    whale_subs = [s for s in active if "whales" in s.feeds]
    if whale_subs:
        plan["whales"] = {"type": ["swap"], "min_volume_usd": min(s.whale_usd for s in whale_subs)}
    watched = set().union(*(s.watched for s in active)) if active else set()
    if watched:
        plan["watch"] = {"type": ["swap"], "address": sorted(watched)}
    return plan
