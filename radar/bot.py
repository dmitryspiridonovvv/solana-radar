"""Telegram front end: commands to pick feeds, thresholds and watched tokens."""

import re

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .alerts import FEEDS, MAX_WATCHED_MINTS, MIN_WHALE_USD, usd
from .engine import RadarEngine
from .reports import stats_report, token_report
from .storage import SubscriptionStore

MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")  # base58 Solana address

HELP = (
    "<b>Solana Radar</b> - live alerts from decoded Solana market data.\n\n"
    "/feeds - choose what to receive\n"
    "/whale 25000 - minimum USD size for whale swaps\n"
    "/watch &lt;mint&gt; - alert on every swap of a token\n"
    "/unwatch &lt;mint&gt; · /watchlist\n"
    "/token &lt;mint&gt; - price, liquidity, holders, dev history\n"
    "/stats - last-hour market activity and stream health\n"
    "/pause · /resume"
)


def feeds_keyboard(active: set) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{'✅' if key in active else '▫️'} {key}: {desc}", callback_data=f"feed:{key}")]
        for key, desc in FEEDS.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_router(store: SubscriptionStore, engine: RadarEngine) -> Router:
    router = Router()

    async def changed() -> None:
        await engine.sync_streams()

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        sub = store.get_or_create(message.chat.id)
        await changed()
        await message.answer(
            HELP + f"\n\nYou're subscribed to: {', '.join(sorted(sub.feeds))} (whales from {usd(sub.whale_usd)}).",
            disable_web_page_preview=True,
        )

    @router.message(Command("help"))
    async def help_(message: Message) -> None:
        await message.answer(HELP)

    @router.message(Command("feeds"))
    async def feeds(message: Message) -> None:
        sub = store.get_or_create(message.chat.id)
        await message.answer("Tap to toggle a feed:", reply_markup=feeds_keyboard(sub.feeds))

    @router.callback_query(F.data.startswith("feed:"))
    async def toggle_feed(callback: CallbackQuery) -> None:
        key = callback.data.split(":", 1)[1]
        if key not in FEEDS:
            await callback.answer("Unknown feed", show_alert=True)
            return
        sub = store.get_or_create(callback.message.chat.id)
        sub.feeds ^= {key}
        store.save(sub)
        await changed()
        await callback.message.edit_reply_markup(reply_markup=feeds_keyboard(sub.feeds))
        await callback.answer(f"{key} {'on' if key in sub.feeds else 'off'}")

    @router.message(Command("whale"))
    async def whale(message: Message, command: CommandObject) -> None:
        sub = store.get_or_create(message.chat.id)
        raw = (command.args or "").replace(",", "").replace("$", "").strip()
        try:
            value = float(raw)
        except ValueError:
            await message.answer(f"Current threshold: {usd(sub.whale_usd)}. Usage: /whale 25000")
            return
        if value < MIN_WHALE_USD:
            await message.answer(f"Minimum is {usd(MIN_WHALE_USD)} - below that the feed is mostly noise.")
            return
        sub.whale_usd = value
        sub.feeds.add("whales")
        store.save(sub)
        await changed()
        await message.answer(f"Whale alerts from {usd(value)}.")

    @router.message(Command("watch"))
    async def watch(message: Message, command: CommandObject) -> None:
        mint = (command.args or "").strip()
        if not MINT_RE.match(mint):
            await message.answer("Usage: /watch &lt;token mint address&gt;")
            return
        sub = store.get_or_create(message.chat.id)
        if mint not in sub.watched and len(sub.watched) >= MAX_WATCHED_MINTS:
            await message.answer(f"Watchlist is full ({MAX_WATCHED_MINTS}). Remove one with /unwatch.")
            return
        sub.watched.add(mint)
        store.save(sub)
        await changed()
        await message.answer(f"Watching <code>{mint}</code> - every swap will be posted here.")

    @router.message(Command("unwatch"))
    async def unwatch(message: Message, command: CommandObject) -> None:
        mint = (command.args or "").strip()
        sub = store.get_or_create(message.chat.id)
        if mint not in sub.watched:
            await message.answer("That token isn't on your watchlist. See /watchlist.")
            return
        sub.watched.discard(mint)
        store.save(sub)
        await changed()
        await message.answer(f"Stopped watching <code>{mint}</code>.")

    @router.message(Command("watchlist"))
    async def watchlist(message: Message) -> None:
        sub = store.get_or_create(message.chat.id)
        if not sub.watched:
            await message.answer("Watchlist is empty. Add a token with /watch &lt;mint&gt;.")
            return
        await message.answer("\n".join(f"<code>{m}</code>" for m in sorted(sub.watched)))

    @router.message(Command("token"))
    async def token(message: Message, command: CommandObject) -> None:
        mint = (command.args or "").strip()
        if not MINT_RE.match(mint):
            await message.answer("Usage: /token &lt;token mint address&gt;")
            return
        if engine.rest is None:
            await message.answer("Token lookups are not configured.")
            return
        try:
            data = await engine.rest.token_full(mint)
        except Exception as exc:
            await message.answer(f"Lookup failed: {exc}")
            return
        await message.answer(token_report(data), disable_web_page_preview=True)

    @router.message(Command("stats"))
    async def stats(message: Message) -> None:
        streams = {name: s.connected.is_set() for name, s in engine.streams.items()}
        await message.answer(stats_report(engine.metrics.snapshot(), streams, engine.fatal_errors(), engine.router.names), disable_web_page_preview=True)

    @router.message(Command("pause"))
    async def pause(message: Message) -> None:
        sub = store.get_or_create(message.chat.id)
        sub.paused = True
        store.save(sub)
        await changed()
        await message.answer("Paused. /resume to turn alerts back on.")

    @router.message(Command("resume"))
    async def resume(message: Message) -> None:
        sub = store.get_or_create(message.chat.id)
        sub.paused = False
        store.save(sub)
        await changed()
        await message.answer("Alerts are back on.")

    return router
