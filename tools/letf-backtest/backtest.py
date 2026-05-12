#!/usr/bin/env python3
"""LETF + moving-average regime-filter backtest.

Synthesizes a daily-reset N×-leveraged ETF from the underlying's daily returns
(applying expense ratio + financing cost on the borrowed portion), then runs
the classic "hold LETF when underlying > SMA(window), else cash" filter and
reports CAGR, max drawdown, vol, Sharpe, and time-in-market vs buy-and-hold.

Why synthesize: real LETF history is short (TQQQ since 2010, SOXL since 2010)
and you usually want to test pre-2010 regimes too. The synthesis is a known
good approximation for daily-reset products.

Examples:
    python3 backtest.py --underlying QQQ --leverage 3 --sma 200
    python3 backtest.py --underlying SOXX --leverage 3 --sma 100 --start 2010-01-01
    python3 backtest.py --csv my_prices.csv --leverage 3 --sma 200
    python3 backtest.py --underlying QQQ --leverage 3 --sma 50,100,200    # sweep
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_yfinance(symbol: str, start: str | None, end: str | None) -> pd.Series:
    try:
        import yfinance as yf
    except ImportError:
        sys.exit("ERROR: yfinance not installed. pip install yfinance — or use --csv.")
    df = yf.download(symbol, start=start, end=end, auto_adjust=True, progress=False)
    if df.empty:
        sys.exit(f"ERROR: yfinance returned no data for {symbol}.")
    close = df["Close"]
    if isinstance(close, pd.DataFrame):  # multi-symbol shape
        close = close.iloc[:, 0]
    close.name = symbol
    return close.dropna()


def load_csv(path: Path, price_col: str = "Close") -> pd.Series:
    df = pd.read_csv(path)
    date_col = next((c for c in df.columns if c.lower() in {"date", "datetime", "timestamp"}), None)
    if date_col is None:
        sys.exit("ERROR: CSV must have a Date/Datetime column.")
    if price_col not in df.columns:
        sys.exit(f"ERROR: CSV missing '{price_col}' column. Found: {list(df.columns)}")
    s = pd.Series(df[price_col].values, index=pd.to_datetime(df[date_col]), name=price_col)
    return s.sort_index().dropna()


# ---------------------------------------------------------------------------
# LETF synthesis
# ---------------------------------------------------------------------------

@dataclass
class LETFParams:
    leverage: float = 3.0        # daily reset multiple
    expense_ratio: float = 0.0095  # 0.95% per year (TQQQ ~0.84%, SOXL ~0.75%)
    financing_rate: float = 0.05   # short rate proxy for the borrowed (L-1)x

    @property
    def daily_drag(self) -> float:
        """Per-trading-day cost: expenses + financing on the borrowed portion."""
        annual = self.expense_ratio + (self.leverage - 1) * self.financing_rate
        return annual / 252


def synthesize_letf(close: pd.Series, p: LETFParams) -> pd.Series:
    """Return synthetic LETF daily returns from underlying close series."""
    r = close.pct_change()
    return (p.leverage * r) - p.daily_drag


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

def ma_filter_returns(
    underlying_close: pd.Series,
    letf_returns: pd.Series,
    sma_window: int,
    cash_yield: float = 0.0,
) -> tuple[pd.Series, pd.Series]:
    """Hold LETF when yesterday's underlying close > yesterday's SMA, else cash.

    Returns (strategy_daily_returns, in_market_signal).
    """
    sma = underlying_close.rolling(sma_window).mean()
    in_market = (underlying_close.shift(1) > sma.shift(1)).fillna(False)
    cash_daily = cash_yield / 252
    strat = letf_returns.where(in_market, cash_daily)
    return strat.fillna(0.0), in_market


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

@dataclass
class Metrics:
    name: str
    cagr: float
    mdd: float
    vol: float
    sharpe: float
    final_multiple: float
    time_in_market: float = 1.0  # fraction of days exposed

    def row(self) -> list[str]:
        return [
            self.name,
            f"{self.cagr * 100:>6.2f}%",
            f"{self.mdd * 100:>6.1f}%",
            f"{self.vol * 100:>5.1f}%",
            f"{self.sharpe:>5.2f}",
            f"{self.final_multiple:>5.2f}x",
            f"{self.time_in_market * 100:>5.1f}%",
        ]


def compute_metrics(name: str, returns: pd.Series, in_market: pd.Series | None = None) -> Metrics:
    r = returns.dropna()
    cum = (1.0 + r).cumprod()
    years = len(r) / 252
    cagr = cum.iloc[-1] ** (1 / years) - 1 if years > 0 else 0.0
    mdd = ((cum / cum.cummax()) - 1).min()
    vol = r.std() * np.sqrt(252)
    sharpe = (r.mean() * 252) / vol if vol > 0 else 0.0
    tim = float(in_market.mean()) if in_market is not None else 1.0
    return Metrics(name, cagr, float(mdd), float(vol), float(sharpe), float(cum.iloc[-1]), tim)


def print_table(rows: list[Metrics]) -> None:
    headers = ["Strategy", "CAGR", "MDD", "Vol", "Sharpe", "Final", "TIM"]
    body = [m.row() for m in rows]
    widths = [max(len(h), *(len(r[i]) for r in body)) for i, h in enumerate(headers)]
    line = "  ".join(h.ljust(w) for h, w in zip(headers, widths))
    print(line)
    print("  ".join("-" * w for w in widths))
    for r in body:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_int_list(spec: str) -> list[int]:
    return [int(x.strip()) for x in spec.split(",") if x.strip()]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--underlying", help="Underlying symbol via yfinance (e.g. QQQ, SOXX, SPY)")
    src.add_argument("--csv", type=Path, help="CSV with Date + Close columns")
    p.add_argument("--start", default=None, help="YYYY-MM-DD (yfinance only)")
    p.add_argument("--end", default=None, help="YYYY-MM-DD (yfinance only)")
    p.add_argument("--leverage", type=float, default=3.0)
    p.add_argument("--expense", type=float, default=0.0095, help="Annual expense ratio (default 0.95%)")
    p.add_argument("--financing", type=float, default=0.05, help="Annual financing rate on borrowed (L-1)x")
    p.add_argument("--sma", type=parse_int_list, default=[200],
                   help="SMA window(s) for the filter, comma-separated (e.g. 50,100,200)")
    p.add_argument("--cash-yield", type=float, default=0.0,
                   help="Annual yield when out of market (e.g. 0.045 for T-bills)")
    p.add_argument("--out-csv", type=Path, default=None,
                   help="Optional: write daily equity curves to CSV")
    args = p.parse_args()

    close = load_yfinance(args.underlying, args.start, args.end) if args.underlying else load_csv(args.csv)
    name = args.underlying or args.csv.stem
    print(f"loaded {len(close)} daily closes for {name} "
          f"({close.index[0].date()} → {close.index[-1].date()})", file=sys.stderr)

    params = LETFParams(leverage=args.leverage, expense_ratio=args.expense, financing_rate=args.financing)
    underlying_returns = close.pct_change().fillna(0.0)
    letf_returns = synthesize_letf(close, params).fillna(0.0)

    rows: list[Metrics] = [
        compute_metrics(f"{name} (1x B&H)", underlying_returns),
        compute_metrics(f"{name} {args.leverage:g}x synth B&H", letf_returns),
    ]

    curves = pd.DataFrame({
        f"{name}_1x": (1 + underlying_returns).cumprod(),
        f"{name}_{args.leverage:g}x_BH": (1 + letf_returns).cumprod(),
    })

    for w in args.sma:
        strat, in_mkt = ma_filter_returns(close, letf_returns, w, cash_yield=args.cash_yield)
        rows.append(compute_metrics(f"{args.leverage:g}x + SMA{w} filter", strat, in_mkt))
        curves[f"{args.leverage:g}x_SMA{w}"] = (1 + strat).cumprod()

    print()
    print_table(rows)
    print()

    if args.out_csv:
        curves.to_csv(args.out_csv)
        print(f"wrote equity curves to {args.out_csv}", file=sys.stderr)


if __name__ == "__main__":
    main()
