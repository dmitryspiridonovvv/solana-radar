"""Text for /token and /stats."""

from html import escape

from .alerts import links, short, usd
from .metrics import num


def token_report(data: dict) -> str:
    mint = data.get("mint", "")
    symbol = escape(data.get("symbol") or "?")
    name = escape(data.get("name") or "")
    screener = data.get("screener") or {}
    stats = data.get("stats") or {}
    day = stats.get("86400") or stats.get("3600") or {}
    window = "24h" if "86400" in stats else "1h"
    lines = [
        f"<b>${symbol}</b> {name}",
        f"<code>{mint}</code>",
        f"Price {usd(data.get('price_usd'))} · Mcap {usd(data.get('market_cap_usd'))} · ATH mcap {usd(data.get('ath_mcap_usd'))}",
        f"Liquidity {usd(data.get('liquidity_usd'))} · Holders {data.get('holders', '?')} · Top-10 {num(data.get('top10_pct')):.1f}%",
    ]
    if day:
        lines.append(
            f"{window}: volume {usd(day.get('volume_usd'))} · {day.get('trades', 0)} trades "
            f"({day.get('buys', 0)} buys / {day.get('sells', 0)} sells) · {day.get('traders', 0)} traders"
        )
    if screener:
        state = "graduated" if screener.get("is_graduated") else f"bonding {num(screener.get('bonding_pct')):.0f}%"
        lines.append(f"Launchpad {escape(str(screener.get('launchpad') or '-'))} · {state} · organic score {num(screener.get('organic_score')):.0f}")
    dev = data.get("dev") or {}
    if dev:
        lines.append(f"Dev <code>{short(dev.get('wallet'))}</code> launched {dev.get('tokens_launched', '?')} tokens")
    pools = data.get("pools") or []
    if pools:
        best = max(pools, key=lambda p: num(p.get("liquidity_usd")))
        lines.append(f"Main pool {escape(str(best.get('dex')))} · TVL {usd(best.get('tvl_usd'))} · LP burned {best.get('lp_burn_pct') or '?'}%")
    lines.append(links(mint))
    return "\n".join(lines)


def stats_report(snap: dict, streams: dict[str, bool], fatal: dict[str, str]) -> str:
    uptime = snap["uptime_secs"]
    lines = [
        "<b>Solana Radar - last hour</b>",
        f"Launches {snap['launches_1h']} · Graduations {snap['graduations_1h']} · Breakouts {snap['surges_1h']}",
        f"Tracked swaps {snap['whale_swaps_1h']}: buys {usd(snap['whale_buy_usd_1h'])} / sells {usd(snap['whale_sell_usd_1h'])}",
    ]
    if snap["top_surges"]:
        tops = ", ".join(f"<code>{short(s['mint'])}</code> x{s['multiple']}" for s in snap["top_surges"])
        lines.append(f"Top breakouts: {tops}")
    lag = f"{snap['lag_p50_secs']}s / p95 {snap['lag_p95_secs']}s" if snap["lag_p50_secs"] is not None else "n/a"
    state = ", ".join(f"{name} {'🟢' if ok else '🔴'}" for name, ok in streams.items()) or "no active streams"
    lines += [
        "",
        "<b>Stream health</b>",
        f"Streams: {state}",
        f"Events: {snap['events_total']} total, {snap['events_per_min']}/min · {snap['mb_received']} MB",
        f"Block-to-bot lag: {lag} · reconnects {snap['reconnects']}",
        f"Alerts sent {snap['alerts_sent']} · rate-limited {snap['alerts_dropped']} · uptime {uptime // 3600}h {uptime % 3600 // 60}m",
    ]
    for name, error in fatal.items():
        lines.append(f"⚠️ {name}: {escape(error)}")
    return "\n".join(lines)
