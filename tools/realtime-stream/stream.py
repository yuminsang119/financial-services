#!/usr/bin/env python3
"""Real-time leveraged-ETF streaming via Polygon.io WebSocket.

Examples:
    POLYGON_API_KEY=xxx python3 stream.py
    POLYGON_API_KEY=xxx python3 stream.py --tickers SOXL,TQQQ,SQQQ --feed trades
    POLYGON_API_KEY=xxx python3 stream.py --feed aggregates --log-dir ./out
    POLYGON_API_KEY=xxx python3 stream.py --delayed                   # free 15-min feed
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import websockets

LIVE_URL = "wss://socket.polygon.io/stocks"
DELAYED_URL = "wss://delayed.polygon.io/stocks"

FEED_PREFIXES = {
    "trades": "T",
    "quotes": "Q",
    "aggregates": "A",         # per-second OHLCV
    "minute_aggregates": "AM", # per-minute OHLCV
}

C = {
    "green": "\033[32m", "red": "\033[31m", "dim": "\033[2m",
    "bold": "\033[1m", "reset": "\033[0m",
}


def fmt_ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%H:%M:%S.%f")[:-3]


def render_trade(ev: dict, last: dict[str, float]) -> str:
    sym, price, size = ev["sym"], ev["p"], ev["s"]
    prev = last.get(sym)
    arrow, color = "·", C["dim"]
    if prev is not None:
        if price > prev:
            arrow, color = "▲", C["green"]
        elif price < prev:
            arrow, color = "▼", C["red"]
    last[sym] = price
    return (
        f"{C['dim']}{fmt_ts(ev['t'])}{C['reset']}  "
        f"{C['bold']}{sym:<5}{C['reset']}  "
        f"{color}{arrow} ${price:>8.4f}{C['reset']}  size={size:>6}"
    )


def render_quote(ev: dict, _last: dict[str, float]) -> str:
    bp, ap = ev.get("bp", 0.0), ev.get("ap", 0.0)
    return (
        f"{C['dim']}{fmt_ts(ev['t'])}{C['reset']}  "
        f"{C['bold']}{ev['sym']:<5}{C['reset']}  "
        f"bid {bp:.4f}×{ev.get('bs', 0):>5}  "
        f"ask {ap:.4f}×{ev.get('as', 0):>5}  "
        f"spread {ap - bp:.4f}"
    )


def render_aggregate(ev: dict, _last: dict[str, float]) -> str:
    o, h, l, c, v = ev["o"], ev["h"], ev["l"], ev["c"], ev["v"]
    chg = (c - o) / o * 100 if o else 0.0
    color = C["green"] if chg >= 0 else C["red"]
    return (
        f"{C['dim']}{fmt_ts(ev['s'])}{C['reset']}  "
        f"{C['bold']}{ev['sym']:<5}{C['reset']}  "
        f"O={o:.4f} H={h:.4f} L={l:.4f} C={c:.4f}  "
        f"{color}{chg:+.2f}%{C['reset']}  vol={v}"
    )


RENDERERS = {
    "T": render_trade, "Q": render_quote,
    "A": render_aggregate, "AM": render_aggregate,
}


async def run(api_key: str, tickers: list[str], feed: str, log_path: Path | None, delayed: bool) -> None:
    url = DELAYED_URL if delayed else LIVE_URL
    prefix = FEED_PREFIXES[feed]
    sub_params = ",".join(f"{prefix}.{t}" for t in tickers)
    last_price: dict[str, float] = {}
    log_fh = log_path.open("a", buffering=1) if log_path else None

    print(f"{C['dim']}connecting to {url} …{C['reset']}", file=sys.stderr)
    async for ws in websockets.connect(url, ping_interval=20, ping_timeout=20):
        try:
            _ = await ws.recv()  # status: connected
            await ws.send(json.dumps({"action": "auth", "params": api_key}))
            auth = json.loads(await ws.recv())
            if not (isinstance(auth, list) and auth[0].get("status") == "auth_success"):
                raise RuntimeError(f"auth failed: {auth}")
            print(f"{C['dim']}authed.{C['reset']}", file=sys.stderr)

            await ws.send(json.dumps({"action": "subscribe", "params": sub_params}))
            sub = json.loads(await ws.recv())
            print(f"{C['dim']}subscribed: {sub}{C['reset']}", file=sys.stderr)
            print(
                f"{C['dim']}streaming {feed} for {','.join(tickers)} "
                f"— Ctrl+C to stop{C['reset']}",
                file=sys.stderr,
            )

            async for raw in ws:
                for ev in json.loads(raw):
                    renderer = RENDERERS.get(ev.get("ev"))
                    if renderer is None:
                        continue
                    print(renderer(ev, last_price))
                    if log_fh:
                        log_fh.write(json.dumps(ev, separators=(",", ":")) + "\n")
        except websockets.ConnectionClosed:
            print(f"{C['dim']}connection closed; reconnecting …{C['reset']}", file=sys.stderr)
            continue


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tickers", default="SOXL,TQQQ", help="Comma-separated tickers (default: SOXL,TQQQ)")
    p.add_argument("--feed", default="trades", choices=list(FEED_PREFIXES))
    p.add_argument("--log-dir", default="./out", help="JSONL log dir (empty string disables)")
    p.add_argument("--delayed", action="store_true", help="Use 15-min delayed endpoint")
    args = p.parse_args()

    api_key = os.environ.get("POLYGON_API_KEY")
    if not api_key:
        sys.exit("ERROR: set POLYGON_API_KEY env var")

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    log_path: Path | None = None
    if args.log_dir:
        log_dir = Path(args.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        log_path = log_dir / f"polygon-{args.feed}-{stamp}.jsonl"
        print(f"logging ticks to {log_path}", file=sys.stderr)

    try:
        asyncio.run(run(api_key, tickers, args.feed, log_path, args.delayed))
    except KeyboardInterrupt:
        print("\nstopped.", file=sys.stderr)


if __name__ == "__main__":
    main()
