"""
Backtest the SMA-crossover strategy (strategy.py) against historical OHLCV data.

Reuses the exact same `simple_moving_average` / `detect_crossover_signal`
functions the live bots (bot.py for ccxt spot, mt5_bot.py for MetaTrader 5)
use, so a backtest result reflects what a live bot would actually have
done — not a separate reimplementation that could drift.

Simulation stays within the same Shariah constraints as the live bot: it is
a cash-based spot simulation (starts with a quote-currency balance, buys
spend cash it has, sells liquidate only the base balance actually held), so
there is no leverage and no shorting to backtest in the first place.

Two data sources:
  --csv FILE            a local OHLCV CSV (timestamp,open,high,low,close,volume)
  --exchange ID --since  historical candles fetched live via ccxt (spot only)

This tool does not place any orders — it only reads historical price data
and reports how the strategy would have performed.
"""

from __future__ import annotations

import argparse
import csv
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Sequence

from strategy import Signal, detect_crossover_signal, simple_moving_average

log = logging.getLogger("shariah_gold_backtest")

Candle = list  # [timestamp_ms, open, high, low, close, volume]


# --------------------------------------------------------------------------
# Historical data loading
# --------------------------------------------------------------------------

def _parse_timestamp(value: str) -> int:
    """Parse an epoch-milliseconds string, or an ISO 8601 / YYYY-MM-DD date,
    into epoch milliseconds (UTC)."""
    value = value.strip()
    try:
        return int(value)
    except ValueError:
        pass

    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def _format_timestamp(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def load_ohlcv_csv(path: str) -> list[Candle]:
    """Load candles from a CSV with timestamp,open,high,low,close,volume columns.

    `timestamp` may be epoch milliseconds or an ISO 8601 / YYYY-MM-DD date.
    """
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    candles: list[Candle] = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV is missing required columns: {sorted(missing)}")
        for row in reader:
            candles.append(
                [
                    _parse_timestamp(row["timestamp"]),
                    float(row["open"]),
                    float(row["high"]),
                    float(row["low"]),
                    float(row["close"]),
                    float(row["volume"]),
                ]
            )

    if not candles:
        raise ValueError(f"No rows found in {path}")

    candles.sort(key=lambda c: c[0])
    return candles


def fetch_historical_ohlcv(
    exchange_id: str,
    symbol: str,
    timeframe: str,
    since_ms: int,
    until_ms: Optional[int] = None,
    limit: int = 1000,
) -> list[Candle]:
    """Fetch historical candles from a spot market via ccxt, paginating with `since`."""
    import ccxt  # imported lazily so CSV-only usage never requires network setup

    exchange_class = getattr(ccxt, exchange_id)
    exchange = exchange_class({"enableRateLimit": True, "options": {"defaultType": "spot"}})
    exchange.load_markets()

    market = exchange.market(symbol)
    if not market.get("spot", False):
        raise ValueError(
            f"{symbol} on {exchange_id} is not a spot market (type={market.get('type')!r})."
        )

    all_candles: list[Candle] = []
    since = since_ms
    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since, limit=limit)
        if not batch:
            break
        all_candles.extend(batch)
        last_ts = batch[-1][0]
        if until_ms is not None and last_ts >= until_ms:
            break
        if len(batch) < limit:
            break
        since = last_ts + 1

    if until_ms is not None:
        all_candles = [c for c in all_candles if c[0] <= until_ms]
    return all_candles


# --------------------------------------------------------------------------
# Backtest simulation
# --------------------------------------------------------------------------

@dataclass
class BacktestConfig:
    short_window: int = 10
    long_window: int = 30
    starting_quote_balance: float = 1000.0
    quote_amount_per_buy: float = 50.0
    fee_rate: float = 0.001  # approximate spot taker fee, applied on both buys and sells

    def __post_init__(self) -> None:
        if self.short_window < 1 or self.long_window < 1:
            raise ValueError("SMA windows must be positive integers")
        if self.short_window >= self.long_window:
            raise ValueError("short_window must be smaller than long_window")
        if self.starting_quote_balance <= 0:
            raise ValueError("starting_quote_balance must be positive")
        if self.quote_amount_per_buy <= 0:
            raise ValueError("quote_amount_per_buy must be positive")
        if not (0 <= self.fee_rate < 1):
            raise ValueError("fee_rate must be in [0, 1)")


@dataclass
class Trade:
    index: int
    timestamp: int
    side: str  # "buy" or "sell"
    price: float
    base_amount: float
    quote_value: float  # quote spent (buy) or received (sell), after fees


@dataclass
class BacktestResult:
    candles: list[Candle]
    trades: list[Trade]
    equity_curve: list[float]
    final_quote_balance: float
    final_base_balance: float

    @property
    def final_price(self) -> float:
        return self.candles[-1][4]

    @property
    def final_equity(self) -> float:
        return self.final_quote_balance + self.final_base_balance * self.final_price


def run_backtest(candles: Sequence[Candle], config: BacktestConfig) -> BacktestResult:
    """Replay the SMA-crossover strategy over historical candles.

    Buys spend up to `quote_amount_per_buy` of the available cash balance;
    sells liquidate the entire base-asset position (go flat) — the same
    behavior as the live bot's `place_spot_buy` / `place_spot_sell_to_flat`,
    so a position can never go negative (no shorting) and no cash is ever
    borrowed (no leverage).
    """
    if len(candles) < config.long_window + 1:
        raise ValueError("not enough candles for the given long_window")

    closes = [c[4] for c in candles]
    short_sma = simple_moving_average(closes, config.short_window)
    long_sma = simple_moving_average(closes, config.long_window)

    quote_balance = config.starting_quote_balance
    base_balance = 0.0
    trades: list[Trade] = []
    equity_curve: list[float] = [quote_balance]

    for i in range(1, len(candles)):
        price = closes[i]
        signal: Signal = detect_crossover_signal(short_sma[i - 1 : i + 1], long_sma[i - 1 : i + 1])

        if signal == "BUY" and quote_balance > 0:
            spend = min(config.quote_amount_per_buy, quote_balance)
            base_bought = (spend * (1 - config.fee_rate)) / price
            quote_balance -= spend
            base_balance += base_bought
            trades.append(Trade(i, candles[i][0], "buy", price, base_bought, spend))
        elif signal == "SELL" and base_balance > 0:
            proceeds = base_balance * price * (1 - config.fee_rate)
            trades.append(Trade(i, candles[i][0], "sell", price, base_balance, proceeds))
            quote_balance += proceeds
            base_balance = 0.0

        equity_curve.append(quote_balance + base_balance * price)

    return BacktestResult(
        candles=list(candles),
        trades=trades,
        equity_curve=equity_curve,
        final_quote_balance=quote_balance,
        final_base_balance=base_balance,
    )


def max_drawdown_pct(equity_curve: Sequence[float]) -> float:
    peak = equity_curve[0]
    worst = 0.0
    for value in equity_curve:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, (peak - value) / peak)
    return worst * 100


def summarize(result: BacktestResult, config: BacktestConfig) -> dict:
    closes = [c[4] for c in result.candles]
    final_equity = result.final_equity
    total_return_pct = (final_equity - config.starting_quote_balance) / config.starting_quote_balance * 100

    buy_hold_base = (config.starting_quote_balance * (1 - config.fee_rate)) / closes[0]
    buy_hold_equity = buy_hold_base * closes[-1]
    buy_hold_return_pct = (buy_hold_equity - config.starting_quote_balance) / config.starting_quote_balance * 100

    round_trip_pnls: list[float] = []
    open_buy: Optional[Trade] = None
    for trade in result.trades:
        if trade.side == "buy":
            open_buy = trade
        elif trade.side == "sell" and open_buy is not None:
            round_trip_pnls.append(trade.quote_value - open_buy.quote_value)
            open_buy = None

    wins = sum(1 for pnl in round_trip_pnls if pnl > 0)
    win_rate_pct = (wins / len(round_trip_pnls) * 100) if round_trip_pnls else 0.0

    return {
        "starting_quote_balance": config.starting_quote_balance,
        "final_equity": final_equity,
        "total_return_pct": total_return_pct,
        "buy_and_hold_return_pct": buy_hold_return_pct,
        "num_trades": len(result.trades),
        "num_round_trips": len(round_trip_pnls),
        "win_rate_pct": win_rate_pct,
        "max_drawdown_pct": max_drawdown_pct(result.equity_curve),
        "open_position_base": result.final_base_balance,
    }


def write_equity_curve_csv(path: str, result: BacktestResult) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "equity"])
        for candle, equity in zip(result.candles, result.equity_curve):
            writer.writerow([candle[0], f"{equity:.8f}"])


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--csv", help="Path to an OHLCV CSV file (timestamp,open,high,low,close,volume)")
    source.add_argument("--exchange", help="ccxt exchange id to fetch historical candles from, e.g. binance")

    parser.add_argument("--symbol", default="PAXG/USDT", help="Spot symbol (used with --exchange)")
    parser.add_argument("--timeframe", default="1h", help="Candle timeframe (used with --exchange)")
    parser.add_argument("--since", help="Start date, YYYY-MM-DD or ISO 8601 (required with --exchange)")
    parser.add_argument("--until", help="End date, YYYY-MM-DD or ISO 8601 (used with --exchange)")

    parser.add_argument("--short-window", type=int, default=10)
    parser.add_argument("--long-window", type=int, default=30)
    parser.add_argument("--starting-balance", type=float, default=1000.0)
    parser.add_argument("--quote-amount-per-buy", type=float, default=50.0)
    parser.add_argument("--fee-rate", type=float, default=0.001, help="Fraction, e.g. 0.001 = 0.1%%")
    parser.add_argument("--csv-out", default=None, help="Optional path to write the equity curve as CSV")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)

    if args.csv:
        candles = load_ohlcv_csv(args.csv)
    else:
        if not args.since:
            raise SystemExit("--since is required when using --exchange")
        since_ms = _parse_timestamp(args.since)
        until_ms = _parse_timestamp(args.until) if args.until else None
        candles = fetch_historical_ohlcv(args.exchange, args.symbol, args.timeframe, since_ms, until_ms)

    config = BacktestConfig(
        short_window=args.short_window,
        long_window=args.long_window,
        starting_quote_balance=args.starting_balance,
        quote_amount_per_buy=args.quote_amount_per_buy,
        fee_rate=args.fee_rate,
    )
    result = run_backtest(candles, config)
    stats = summarize(result, config)

    print(f"Candles:            {len(candles)}")
    print(f"Period:             {_format_timestamp(candles[0][0])} -> {_format_timestamp(candles[-1][0])}")
    print(f"SMA windows:        short={config.short_window} long={config.long_window}")
    print(f"Starting balance:   {stats['starting_quote_balance']:.2f}")
    print(f"Final equity:       {stats['final_equity']:.2f}")
    print(f"Total return:       {stats['total_return_pct']:.2f}%")
    print(f"Buy & hold return:  {stats['buy_and_hold_return_pct']:.2f}%")
    print(f"Trades:             {stats['num_trades']} ({stats['num_round_trips']} round trips)")
    print(f"Win rate:           {stats['win_rate_pct']:.2f}%")
    print(f"Max drawdown:       {stats['max_drawdown_pct']:.2f}%")
    if stats["open_position_base"] > 0:
        print(
            f"Note: ended with an open position of {stats['open_position_base']:.6f} base "
            "units (unrealized, marked to the last close above)"
        )

    if args.csv_out:
        write_equity_curve_csv(args.csv_out, result)
        print(f"Equity curve written to {args.csv_out}")


if __name__ == "__main__":
    main()
