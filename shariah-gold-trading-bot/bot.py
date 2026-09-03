"""
Shariah-compliant gold SMA-crossover trading bot.

SHARIAH-COMPLIANCE NOTES (read before using):
- SPOT ONLY. No margin, no leverage, no borrowing to trade. This avoids riba (interest).
- NO SHORT-SELLING. Shorting typically requires borrowing an asset you don't own,
  which most scholars consider non-compliant unless structured via a specific
  Salam/Arboun-style contract. This bot only ever goes long, then flat.
- NO OVERNIGHT SWAP/ROLLOVER FEES. If your broker charges a daily interest-like fee
  for holding positions overnight, that fee is riba. Use an "Islamic" / swap-free
  account, or only trade instruments that settle immediately (spot, not CFDs/futures).
- AVOID EXCESSIVE UNCERTAINTY (gharar). Keep the strategy simple and transparent
  (e.g. moving-average crossover), not opaque black-box derivatives.
- This script is NOT a fatwa. Shariah compliance depends on your specific broker,
  the exact instrument (e.g. tokenized gold vs. a CFD), and jurisdiction. Verify
  with a qualified Islamic finance scholar or a shariah-certified broker before
  trading real funds.
- This is also not financial advice. Trading involves risk of loss.

STRATEGY: Simple moving average (SMA) crossover on spot gold (XAU/USDT-style pair,
adjust to whatever spot commodity pair your exchange lists).
- Buy when the short-term SMA crosses above the long-term SMA (uptrend starting)
- Sell (go flat) when the short-term SMA crosses below the long-term SMA
- No leverage is ever requested; order type is always spot market order

Requires: ccxt (pip install -r requirements.txt)
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from dataclasses import dataclass
from typing import Literal, Optional, Sequence

try:
    import ccxt
except ImportError as exc:  # pragma: no cover - import guard, not a logic branch
    raise SystemExit(
        "ccxt is required. Install it with: pip install -r requirements.txt"
    ) from exc


log = logging.getLogger("shariah_gold_bot")

Signal = Literal["BUY", "SELL", "HOLD"]


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

@dataclass
class BotConfig:
    exchange_id: str = os.environ.get("EXCHANGE_ID", "binance")
    api_key: str = os.environ.get("EXCHANGE_API_KEY", "")
    api_secret: str = os.environ.get("EXCHANGE_API_SECRET", "")
    symbol: str = os.environ.get("SYMBOL", "PAXG/USDT")  # spot tokenized-gold pair
    timeframe: str = os.environ.get("TIMEFRAME", "1h")
    short_window: int = int(os.environ.get("SHORT_WINDOW", "10"))
    long_window: int = int(os.environ.get("LONG_WINDOW", "30"))
    quote_amount_per_buy: float = float(os.environ.get("QUOTE_AMOUNT_PER_BUY", "50"))
    poll_interval_seconds: int = int(os.environ.get("POLL_INTERVAL_SECONDS", "300"))
    dry_run: bool = os.environ.get("DRY_RUN", "true").lower() != "false"

    def __post_init__(self) -> None:
        if self.short_window < 1 or self.long_window < 1:
            raise ValueError("SMA windows must be positive integers")
        if self.short_window >= self.long_window:
            raise ValueError("short_window must be smaller than long_window")
        if self.quote_amount_per_buy <= 0:
            raise ValueError("quote_amount_per_buy must be positive")


# --------------------------------------------------------------------------
# Exchange setup — enforced spot-only, no leverage
# --------------------------------------------------------------------------

def build_exchange(config: BotConfig) -> "ccxt.Exchange":
    """Build a ccxt exchange instance restricted to spot trading.

    Never requests margin or futures products, and never sends a leverage
    parameter — the account's own spot balance is the only source of funds.
    """
    exchange_class = getattr(ccxt, config.exchange_id)
    exchange = exchange_class(
        {
            "apiKey": config.api_key,
            "secret": config.api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "spot"},
        }
    )
    exchange.load_markets()

    market = exchange.market(config.symbol)
    if not market.get("spot", False):
        raise ValueError(
            f"{config.symbol} on {config.exchange_id} is not a spot market "
            f"(type={market.get('type')!r}). Refusing to trade a margin/futures "
            "instrument."
        )

    return exchange


# --------------------------------------------------------------------------
# Strategy: SMA crossover
# --------------------------------------------------------------------------

def simple_moving_average(values: Sequence[float], window: int) -> list[Optional[float]]:
    """Return a same-length list of SMA values; entries before `window` samples
    have accumulated are None."""
    if window < 1:
        raise ValueError("window must be a positive integer")

    sma: list[Optional[float]] = [None] * len(values)
    running_sum = 0.0
    for i, value in enumerate(values):
        running_sum += value
        if i >= window:
            running_sum -= values[i - window]
        if i >= window - 1:
            sma[i] = running_sum / window
    return sma


def detect_crossover_signal(
    short_sma: Sequence[Optional[float]], long_sma: Sequence[Optional[float]]
) -> Signal:
    """Compare the last two bars of the two SMA series to detect a crossover.

    Returns BUY on a fresh golden cross (short crosses above long), SELL on a
    fresh death cross (short crosses below long), otherwise HOLD.
    """
    if len(short_sma) != len(long_sma):
        raise ValueError("short_sma and long_sma must be the same length")
    if len(short_sma) < 2:
        return "HOLD"

    prev_short, prev_long = short_sma[-2], long_sma[-2]
    curr_short, curr_long = short_sma[-1], long_sma[-1]

    if None in (prev_short, prev_long, curr_short, curr_long):
        return "HOLD"

    was_below_or_equal = prev_short <= prev_long
    is_above = curr_short > curr_long
    if was_below_or_equal and is_above:
        return "BUY"

    was_above_or_equal = prev_short >= prev_long
    is_below = curr_short < curr_long
    if was_above_or_equal and is_below:
        return "SELL"

    return "HOLD"


def fetch_signal(exchange: "ccxt.Exchange", config: BotConfig) -> Signal:
    limit = config.long_window + 2
    ohlcv = exchange.fetch_ohlcv(config.symbol, timeframe=config.timeframe, limit=limit)
    closes = [candle[4] for candle in ohlcv]

    short_sma = simple_moving_average(closes, config.short_window)
    long_sma = simple_moving_average(closes, config.long_window)
    return detect_crossover_signal(short_sma, long_sma)


# --------------------------------------------------------------------------
# Order placement — spot market orders only, sells never exceed what is held
# --------------------------------------------------------------------------

def base_asset_free_balance(exchange: "ccxt.Exchange", symbol: str) -> float:
    base, _quote = symbol.split("/")
    balance = exchange.fetch_balance()
    return float(balance.get(base, {}).get("free", 0.0) or 0.0)


def place_spot_buy(exchange: "ccxt.Exchange", config: BotConfig) -> dict:
    """Spend up to `quote_amount_per_buy` of quote currency on a spot market buy.

    Uses createMarketBuyOrder's quote-cost convenience where available so we
    never need to estimate quantity from price ourselves.
    """
    log.info(
        "BUY signal: spending %s %s on %s (spot market order, no leverage)",
        config.quote_amount_per_buy,
        config.symbol.split("/")[1],
        config.symbol,
    )
    if config.dry_run:
        log.info("DRY RUN — no order sent")
        return {"dry_run": True, "side": "buy", "symbol": config.symbol}

    return exchange.create_market_buy_order(
        config.symbol,
        None,
        {"quoteOrderQty": config.quote_amount_per_buy},
    )


def place_spot_sell_to_flat(exchange: "ccxt.Exchange", config: BotConfig) -> Optional[dict]:
    """Sell the entire held base-asset balance (never more) to go flat.

    Selling only what is actually held in the spot wallet is what keeps this
    a "go long, then flat" bot rather than a short: there is never a
    borrowed, negative position.
    """
    held = base_asset_free_balance(exchange, config.symbol)
    if held <= 0:
        log.info("SELL signal but no %s held — nothing to do", config.symbol.split("/")[0])
        return None

    log.info(
        "SELL signal: selling %s %s (spot market order, going flat, no shorting)",
        held,
        config.symbol.split("/")[0],
    )
    if config.dry_run:
        log.info("DRY RUN — no order sent")
        return {"dry_run": True, "side": "sell", "symbol": config.symbol, "amount": held}

    return exchange.create_market_sell_order(config.symbol, held)


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------

def run(config: BotConfig) -> None:
    exchange = build_exchange(config)
    log.info(
        "Started %s SMA(%d/%d) crossover bot on %s [%s] — dry_run=%s",
        config.symbol,
        config.short_window,
        config.long_window,
        config.exchange_id,
        config.timeframe,
        config.dry_run,
    )

    while True:
        try:
            signal = fetch_signal(exchange, config)
            log.info("Signal: %s", signal)
            if signal == "BUY":
                place_spot_buy(exchange, config)
            elif signal == "SELL":
                place_spot_sell_to_flat(exchange, config)
        except Exception:  # noqa: BLE001 - keep the polling loop alive
            log.exception("Error during trading loop iteration")

        time.sleep(config.poll_interval_seconds)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Send real orders (default is dry-run)")
    parser.add_argument("--symbol", default=None, help="Override SYMBOL env var")
    parser.add_argument("--once", action="store_true", help="Evaluate the signal once and exit (no loop)")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)

    config = BotConfig()
    if args.live:
        config.dry_run = False
    if args.symbol:
        config.symbol = args.symbol

    if args.once:
        exchange = build_exchange(config)
        signal = fetch_signal(exchange, config)
        log.info("Signal: %s", signal)
        if signal == "BUY":
            place_spot_buy(exchange, config)
        elif signal == "SELL":
            place_spot_sell_to_flat(exchange, config)
        return

    run(config)


if __name__ == "__main__":
    main()
