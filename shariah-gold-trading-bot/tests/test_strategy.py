import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import (  # noqa: E402
    BotConfig,
    detect_crossover_signal,
    simple_moving_average,
)


def test_simple_moving_average_basic():
    values = [1, 2, 3, 4, 5]
    sma = simple_moving_average(values, window=2)
    assert sma == [None, 1.5, 2.5, 3.5, 4.5]


def test_simple_moving_average_window_larger_than_data():
    assert simple_moving_average([1, 2], window=5) == [None, None]


def test_detect_crossover_signal_golden_cross_triggers_buy():
    short_sma = [10, 9, 11]  # was below/equal, now above
    long_sma = [10, 10, 10]
    assert detect_crossover_signal(short_sma, long_sma) == "BUY"


def test_detect_crossover_signal_death_cross_triggers_sell():
    short_sma = [10, 11, 9]  # was above/equal, now below
    long_sma = [10, 10, 10]
    assert detect_crossover_signal(short_sma, long_sma) == "SELL"


def test_detect_crossover_signal_no_cross_holds():
    short_sma = [12, 13, 14]
    long_sma = [10, 10, 10]
    assert detect_crossover_signal(short_sma, long_sma) == "HOLD"


def test_detect_crossover_signal_needs_two_bars():
    assert detect_crossover_signal([1], [1]) == "HOLD"
    assert detect_crossover_signal([], []) == "HOLD"


def test_detect_crossover_signal_ignores_none_values():
    assert detect_crossover_signal([None, 1], [None, 1]) == "HOLD"


def test_botconfig_rejects_short_window_not_smaller_than_long():
    try:
        BotConfig(short_window=30, long_window=10)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_botconfig_rejects_non_positive_quote_amount():
    try:
        BotConfig(quote_amount_per_buy=0)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
