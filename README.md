# Solana Radar

[![tests](https://github.com/dmitryspiridonovvv/solana-radar/actions/workflows/tests.yml/badge.svg)](https://github.com/dmitryspiridonovvv/solana-radar/actions/workflows/tests.yml)

Live Telegram alerts on decoded Solana market data, built on
[Solami Blur](https://solami.dev/docs/blur). Graduations, tokens about to
graduate, volume breakouts, whale swaps and per-token watchlists, delivered
seconds after they land on mainnet, plus one-command token reports.

Built for the Superteam Earn bounty *Build something live on Solana data*.

## What it does

| Feed | Source (Blur event) | Alert |
|---|---|---|
| `graduations` | `graduation` | A launchpad token just got a real AMM pool |
| `near` | `meme` with `min_progress=90` | Bonding curve at 90%+, announced once per token |
| `surges` | `surge` | 5-minute volume x3+ over the token's last hour |
| `radar` | `radar` | 30-minute steady climb, x1.8 over 6 hours |
| `whales` | `swap` with `min_volume_usd` | Single swap above the chat's USD threshold |
| `launches` | `token_create` | Every new mint (off by default: very noisy) |
| watchlist | `swap` with `address=` | Every swap of the tokens a chat watches |

`/token <mint>` answers from `GET /data/token/full`: price, market cap, ATH,
liquidity, holders, top-10 share, 24h/1h activity, bonding progress, organic
score, dev history and the main pool with LP burn, plus plain-language risk flags computed from
the same response: top-10 concentration, low organic score, dev holdings, serial launchers, unburned LP
and liquidity that is thin for the market cap.

`/stats` answers what the chain did in the last hour and how healthy the feed is:

- launches, graduations and breakouts in the last hour, top breakouts by multiple;
- tracked swap volume split into buys and sells, with outlier prints excluded;
- events per minute, megabytes received, **block-to-bot lag (p50 / p95)**;
- reconnects, alerts sent and alerts rate-limited, per-stream status.

## How it uses Solami

- **Blur WebSocket** (`wss://ws.solami.dev/data/subscribe`) is the data path for every alert.
  Field filters such as `min_volume_usd` or `min_progress` drop every event type they don't apply to,
  so the bot plans one connection per kind of interest (`signals`, `near`, `whales`, `watch`) from the
  union of all chats' settings, and only opens the ones somebody needs.
- **Live filter updates**: when a chat adds a token to its watchlist, the new mint list is pushed over the
  open socket as a `{"filter": {...}}` frame, no reconnect. Filters without a live form reconnect with the new query.
- **Out-of-band `metadata` events** name tokens in alerts; if a name hasn't arrived within 1.5 s, the bot
  falls back to `GET /data/token/metadata`, without blocking the stream.
- **Blur REST** (`api.solami.dev`) powers `/token`.
- Swaps that Blur's price guard marks `candle_ok: false` never count as whales and stay out of volume
  stats (one $585K outlier with 98% price impact showed up within the first minute of live testing);
  watchers still see them, flagged as outliers.
- Alerts are rate-limited per chat with separate budgets for swaps and signals, so a busy whale feed can
  never crowd out a graduation.
- Decimal-string numbers are parsed safely (`null` for non-finite values is handled), close codes 4001/4002 and
  HTTP 401/403 stop the stream with a clear message instead of retrying forever; other drops reconnect with
  jittered exponential backoff.

```
Blur WebSocket ──► BlurStream (one per filter, auto-reconnect)
                        │ decoded events
                        ▼
                  RadarEngine ──► Router (who gets what, dedupe, names) ──► per-chat rate limit ──► Telegram
                        ▲                                   ▲
            subscriptions (SQLite)              Blur REST: /data/token/metadata, /data/token/full
```

## Run it

Requirements: Python 3.11+, a Solami key with the **DataApi** permission
([sign up](https://solami.dev/signup?ref=st-earn-sep-26)), and a bot token from
[@BotFather](https://t.me/BotFather) for Telegram mode.

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in the values
python main.py         # Telegram bot
```

`.env`:

| Variable | Required | Meaning |
|---|---|---|
| `SOLAMI_API_KEY` | yes | Solami key with the DataApi permission |
| `TELEGRAM_BOT_TOKEN` | Telegram mode | Token from @BotFather |
| `DB_PATH` | no | SQLite file for subscriptions, default `radar.db` |
| `LOG_LEVEL` | no | `INFO` by default |

### Console mode (no Telegram needed)

```bash
python main.py --console --feeds graduations,near,surges,whales --whale 20000 --stats-every 60
python main.py --console --feeds whales --watch <MINT1>,<MINT2>
```

Alerts and the `/stats` summary print to the terminal - handy for a quick check of a key or a demo.

### Bot commands

```
/feeds            toggle feeds with buttons
/whale 25000      whale threshold in USD (min 1000)
/watch <mint>     every swap of a token · /unwatch <mint> · /watchlist
/token <mint>     full token report
/stats            last-hour activity and stream health
/pause · /resume
```

## Tests

```bash
pytest
```

47 tests, no network or keys needed. The stream tests run against a local fake of the Blur WebSocket
(`tests/fake_blur.py`) and cover reconnects, rejected keys, the 4002 out-of-bandwidth close, live filter
frames, garbage frames and handler errors. Bot tests drive full Telegram updates through aiogram with a
fake session.

## License

MIT
