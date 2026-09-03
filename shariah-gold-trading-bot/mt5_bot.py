"""
Shariah-compliant gold SMA-crossover trading bot — MetaTrader 5 edition.

READ THIS BEFORE POINTING IT AT A REAL ACCOUNT, DEMO OR OTHERWISE:

- MOST BROKERS' "XAUUSD" ON MT5 IS A CFD, NOT SPOT GOLD. A CFD is a
  leveraged derivative contract with the broker — you never own or take
  delivery of any gold, which is exactly the ownership requirement the
  ccxt spot bot (bot.py) was built to satisfy. Most scholars do NOT
  consider a standard CFD Shariah-compliant, regardless of whether swap
  fees are removed, because of the missing ownership (and the contract
  itself resembles a wagering instrument). If your broker offers a
  genuinely different, physically-backed / spot-settled gold product on
  MT5, verify that directly with them — plain "XAUUSD" almost never is.
- This script therefore checks, at startup and before every order, that
  the symbol's overnight swap rates are exactly zero (an "Islamic" /
  swap-free symbol or account) and refuses to trade otherwise — so at
  minimum you cannot accrue an interest-like overnight charge through it.
  A swap-free CFD is still a CFD. Get a scholar's or shariah-certified
  broker's sign-off on your specific broker and instrument before using
  this beyond a demo account.
- NO SHORT-SELLING: this script only ever opens a BUY position and later
  closes it flat. It never opens a SELL/short position.
- NO LEVERAGE IS REQUESTED BY ANY ORDER: position size (`volume`, in
  lots) is fixed by config, never computed from margin. That said, MT5
  accounts carry a broker-set account-wide leverage ratio (e.g. 1:100)
  that cannot be turned off order-by-order — ask your broker for a 1:1
  account if one is available, and treat this script as a signal/paper-
  trading tool otherwise, not as leverage-free by construction the way
  the spot ccxt bot is.
- This is NOT a fatwa and NOT financial advice. Trading involves risk of
  loss, and CFD trading in particular carries a high risk of rapid loss.

STRATEGY: identical SMA crossover to bot.py (see strategy.py) — buy on a
golden cross, close flat on a death cross.

Requires: MetaTrader5 (pip install MetaTrader5) — Windows only (or Wine).
Also requires a running MT5 terminal, logged into your (demo or live)
account, with Tools > Options > Expert Advisors > "Allow algorithmic
trading" enabled. See README.md for the full demo-account walkthrough.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional, Sequence

try:
    import MetaTrader5 as mt5
except ImportError as exc:  # pragma: no cover - import guard, not a logic branch
    raise SystemExit(
        "MetaTrader5 is required and only installs on Windows (or under Wine). "
        "Install it with: pip install MetaTrader5"
    ) from exc

from strategy import Signal, signal_from_closes

log = logging.getLogger("shariah_gold_mt5_bot")

_TIMEFRAMES = {
    "M1": "TIMEFRAME_M1",
    "M5": "TIMEFRAME_M5",
    "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30",
    "H1": "TIMEFRAME_H1",
    "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
}


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

@dataclass
class MT5BotConfig:
    symbol: str = os.environ.get("MT5_SYMBOL", "XAUUSD")
    timeframe: str = os.environ.get("MT5_TIMEFRAME", "H1")
    short_window: int = int(os.environ.get("SHORT_WINDOW", "10"))
    long_window: int = int(os.environ.get("LONG_WINDOW", "30"))
    volume: float = float(os.environ.get("MT5_VOLUME", "0.01"))  # lots
    magic: int = int(os.environ.get("MT5_MAGIC", "20260903"))
    poll_interval_seconds: int = int(os.environ.get("POLL_INTERVAL_SECONDS", "300"))
    dry_run: bool = os.environ.get("DRY_RUN", "true").lower() != "false"

    def __post_init__(self) -> None:
        if self.short_window < 1 or self.long_window < 1:
            raise ValueError("SMA windows must be positive integers")
        if self.short_window >= self.long_window:
            raise ValueError("short_window must be smaller than long_window")
        if self.volume <= 0:
            raise ValueError("volume must be positive")
        if self.timeframe not in _TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {sorted(_TIMEFRAMES)}")


# --------------------------------------------------------------------------
# Terminal / symbol setup — enforced swap-free, no shorting
# --------------------------------------------------------------------------

def connect(config: MT5BotConfig) -> None:
    if not mt5.initialize():
        raise RuntimeError(f"MetaTrader5 initialize() failed: {mt5.last_error()}")

    account = mt5.account_info()
    if account is None:
        mt5.shutdown()
        raise RuntimeError(f"No MT5 account is logged in: {mt5.last_error()}")

    log.info(
        "Connected to MT5 account #%s (%s), leverage 1:%s — note: account-wide "
        "leverage is set by your broker and this script cannot lower it.",
        account.login,
        account.server,
        account.leverage,
    )

    check_symbol_swap_free(config.symbol)


def check_symbol_swap_free(symbol: str) -> None:
    """Refuse to trade any symbol that carries a nonzero overnight swap rate.

    This is the closest a CFD platform can get to the ccxt spot bot's "no
    riba" guarantee: it does not make the instrument spot (see the
    module-level warning), but it does mean this script will never itself
    cause an interest-like overnight charge to accrue.
    """
    info = mt5.symbol_info(symbol)
    if info is None:
        raise ValueError(f"Symbol {symbol!r} not found or not visible in Market Watch")

    if not mt5.symbol_select(symbol, True):
        raise ValueError(f"Could not select {symbol!r} in Market Watch: {mt5.last_error()}")

    if info.swap_long != 0 or info.swap_short != 0:
        raise ValueError(
            f"{symbol} charges a nonzero overnight swap (long={info.swap_long}, "
            f"short={info.swap_short}). Ask your broker for an Islamic / swap-free "
            "account, or choose a genuinely swap-free symbol. Refusing to trade."
        )


# --------------------------------------------------------------------------
# Strategy
# --------------------------------------------------------------------------

def fetch_signal(config: MT5BotConfig) -> Signal:
    timeframe_const = getattr(mt5, _TIMEFRAMES[config.timeframe])
    count = config.long_window + 2
    rates = mt5.copy_rates_from_pos(config.symbol, timeframe_const, 0, count)
    if rates is None or len(rates) < config.long_window + 1:
        raise RuntimeError(f"Not enough candles returned for {config.symbol}: {mt5.last_error()}")

    closes = [float(r["close"]) for r in rates]
    return signal_from_closes(closes, config.short_window, config.long_window)


# --------------------------------------------------------------------------
# Order placement — long-only: open a BUY, or close an existing BUY to flat
# --------------------------------------------------------------------------

def open_base_position_volume(config: MT5BotConfig) -> float:
    """Total lots currently held long on this symbol under our magic number."""
    positions = mt5.positions_get(symbol=config.symbol) or ()
    return sum(
        p.volume
        for p in positions
        if p.magic == config.magic and p.type == mt5.POSITION_TYPE_BUY
    )


def place_buy(config: MT5BotConfig) -> Optional[dict]:
    check_symbol_swap_free(config.symbol)  # re-check: swap rates can change

    log.info(
        "BUY signal: opening %.2f lots of %s (market order, no leverage requested, no shorting)",
        config.volume,
        config.symbol,
    )
    if config.dry_run:
        log.info("DRY RUN — no order sent")
        return {"dry_run": True, "side": "buy", "symbol": config.symbol, "volume": config.volume}

    tick = mt5.symbol_info_tick(config.symbol)
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": config.symbol,
        "volume": config.volume,
        "type": mt5.ORDER_TYPE_BUY,
        "price": tick.ask,
        "magic": config.magic,
        "comment": "shariah-gold-sma-bot",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_send(request)
    if result.retcode != mt5.TRADE_RETCODE_DONE:
        log.error("BUY order failed: retcode=%s comment=%s", result.retcode, result.comment)
    return result._asdict()


def close_to_flat(config: MT5BotConfig) -> list[dict]:
    """Close every open BUY position on this symbol/magic. Never opens a SELL
    position beyond what is needed to flatten an existing long — there is
    nothing to short."""
    positions = [
        p
        for p in (mt5.positions_get(symbol=config.symbol) or ())
        if p.magic == config.magic and p.type == mt5.POSITION_TYPE_BUY
    ]
    if not positions:
        log.info("SELL signal but no open %s position — nothing to do", config.symbol)
        return []

    log.info(
        "SELL signal: closing %.2f lots of %s (going flat, no shorting)",
        sum(p.volume for p in positions),
        config.symbol,
    )

    if config.dry_run:
        log.info("DRY RUN — no order sent")
        return [
            {"dry_run": True, "side": "sell", "symbol": config.symbol, "volume": p.volume}
            for p in positions
        ]

    results = []
    tick = mt5.symbol_info_tick(config.symbol)
    for position in positions:
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": config.symbol,
            "volume": position.volume,
            "type": mt5.ORDER_TYPE_SELL,
            "position": position.ticket,
            "price": tick.bid,
            "magic": config.magic,
            "comment": "shariah-gold-sma-bot-close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            log.error("Close order failed: retcode=%s comment=%s", result.retcode, result.comment)
        results.append(result._asdict())
    return results


# --------------------------------------------------------------------------
# Main loop
# --------------------------------------------------------------------------

def run(config: MT5BotConfig) -> None:
    connect(config)
    log.info(
        "Started %s SMA(%d/%d) crossover bot on MT5 [%s] — dry_run=%s",
        config.symbol,
        config.short_window,
        config.long_window,
        config.timeframe,
        config.dry_run,
    )

    try:
        while True:
            try:
                signal = fetch_signal(config)
                log.info("Signal: %s", signal)
                if signal == "BUY" and open_base_position_volume(config) == 0:
                    place_buy(config)
                elif signal == "SELL":
                    close_to_flat(config)
            except Exception:  # noqa: BLE001 - keep the polling loop alive
                log.exception("Error during trading loop iteration")

            time.sleep(config.poll_interval_seconds)
    finally:
        mt5.shutdown()


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Send real orders (default is dry-run)")
    parser.add_argument("--symbol", default=None, help="Override MT5_SYMBOL env var")
    parser.add_argument("--once", action="store_true", help="Evaluate the signal once and exit (no loop)")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = parse_args(argv)

    config = MT5BotConfig()
    if args.live:
        config.dry_run = False
    if args.symbol:
        config.symbol = args.symbol

    if args.once:
        connect(config)
        try:
            signal = fetch_signal(config)
            log.info("Signal: %s", signal)
            if signal == "BUY" and open_base_position_volume(config) == 0:
                place_buy(config)
            elif signal == "SELL":
                close_to_flat(config)
        finally:
            mt5.shutdown()
        return

    run(config)


if __name__ == "__main__":
    main()
