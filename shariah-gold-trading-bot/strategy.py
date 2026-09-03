"""
Broker-agnostic SMA-crossover strategy logic, shared by bot.py (ccxt spot),
mt5_bot.py (MetaTrader 5), and backtest.py.

Kept free of any exchange/broker SDK import so it can be unit tested and
reused without pulling in ccxt or MetaTrader5.
"""

from __future__ import annotations

from typing import Literal, Optional, Sequence

Signal = Literal["BUY", "SELL", "HOLD"]


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


def signal_from_closes(closes: Sequence[float], short_window: int, long_window: int) -> Signal:
    """Convenience wrapper: compute both SMAs from a closes series and return
    the crossover signal for the latest bar."""
    short_sma = simple_moving_average(closes, short_window)
    long_sma = simple_moving_average(closes, long_window)
    return detect_crossover_signal(short_sma, long_sma)
