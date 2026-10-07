"""Entry point: Telegram bot by default, or --console to print alerts in the terminal."""

import argparse
import asyncio
import logging
import os
import re
import sys
from html import unescape

from dotenv import load_dotenv

from radar.alerts import DEFAULT_WHALE_USD, FEEDS, Subscription
from radar.engine import RadarEngine
from radar.metrics import Metrics
from radar.reports import stats_report
from radar.rest import BlurRest

CONSOLE_CHAT_ID = 0
BOT_COMMANDS = [
    ("feeds", "Choose alert feeds"),
    ("whale", "Whale swap threshold, USD"),
    ("watch", "Alert on every swap of a token"),
    ("watchlist", "Your watched tokens"),
    ("token", "Full report for a token mint"),
    ("stats", "Last-hour activity and stream health"),
    ("pause", "Pause alerts"),
    ("resume", "Resume alerts"),
]


def plain(html_text: str) -> str:
    text = re.sub(r'<a href="([^"]+)">([^<]+)</a>', r"\2: \1", html_text)
    return unescape(re.sub(r"<[^>]+>", "", text))


async def run_console(api_key: str, feeds: set, whale_usd: float, watch: set, stats_every: int) -> None:
    sub = Subscription(chat_id=CONSOLE_CHAT_ID, feeds=feeds, whale_usd=whale_usd, watched=watch)

    async def send(_chat_id: int, text: str) -> None:
        print(plain(text), end="\n\n", flush=True)

    rest = BlurRest(api_key)
    engine = RadarEngine(api_key, lambda: [sub], send, rest=rest, metrics=Metrics())
    await engine.sync_streams()
    try:
        while True:
            await asyncio.sleep(stats_every)
            streams = {name: s.connected.is_set() for name, s in engine.streams.items()}
            print(plain(stats_report(engine.metrics.snapshot(), streams, engine.fatal_errors(), engine.router.names)), end="\n\n", flush=True)
            if engine.streams and len(engine.fatal_errors()) == len(engine.streams):
                print("All streams stopped with fatal errors - check SOLAMI_API_KEY.")
                return
    finally:
        await engine.stop()
        await rest.close()


async def run_telegram(api_key: str, token: str, db_path: str) -> None:
    from aiogram import Bot, Dispatcher
    from aiogram.client.default import DefaultBotProperties
    from aiogram.types import BotCommand

    from radar.bot import build_router
    from radar.storage import SubscriptionStore

    bot = Bot(token, default=DefaultBotProperties(parse_mode="HTML", link_preview_is_disabled=True))
    store = SubscriptionStore(db_path)
    rest = BlurRest(api_key)

    async def send(chat_id: int, text: str) -> None:
        await bot.send_message(chat_id, text)

    engine = RadarEngine(api_key, store.all, send, rest=rest)
    dp = Dispatcher()
    dp.include_router(build_router(store, engine))
    await bot.set_my_commands([BotCommand(command=c, description=d) for c, d in BOT_COMMANDS])
    await bot.set_my_short_description("Live Solana alerts: graduations, breakouts, whale swaps, watchlists. Powered by Solami Blur.")
    await engine.sync_streams()
    try:
        await dp.start_polling(bot)
    finally:
        await engine.stop()
        await rest.close()
        await bot.session.close()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Solana Radar: live alerts on decoded Solana market data (Solami Blur)")
    parser.add_argument("--console", action="store_true", help="print alerts to the terminal instead of Telegram")
    parser.add_argument("--feeds", default="graduations,surges,whales", help=f"console mode feeds: {','.join(FEEDS)}")
    parser.add_argument("--whale", type=float, default=DEFAULT_WHALE_USD, help="console mode whale threshold, USD")
    parser.add_argument("--watch", default="", help="console mode: comma-separated mints to watch")
    parser.add_argument("--stats-every", type=int, default=60, help="console mode: seconds between stats summaries")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # emoji on Windows consoles
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    api_key = os.environ.get("SOLAMI_API_KEY")
    if not api_key:
        raise SystemExit("Set SOLAMI_API_KEY (a Solami key with the DataApi permission) in .env")

    if args.console:
        feeds = {f.strip() for f in args.feeds.split(",") if f.strip()}
        unknown = feeds - set(FEEDS)
        if unknown:
            raise SystemExit(f"Unknown feeds: {', '.join(sorted(unknown))}")
        watch = {m.strip() for m in args.watch.split(",") if m.strip()}
        asyncio.run(run_console(api_key, feeds, args.whale, watch, args.stats_every))
    else:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            raise SystemExit("Set TELEGRAM_BOT_TOKEN in .env, or run with --console")
        asyncio.run(run_telegram(api_key, token, os.environ.get("DB_PATH", "radar.db")))


if __name__ == "__main__":
    main()
