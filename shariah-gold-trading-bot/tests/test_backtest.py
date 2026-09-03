import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402

from backtest import (  # noqa: E402
    BacktestConfig,
    Trade,
    load_ohlcv_csv,
    max_drawdown_pct,
    run_backtest,
    summarize,
)


def _candles_from_closes(closes, start_ts=1_600_000_000_000, step_ms=3_600_000):
    return [
        [start_ts + i * step_ms, price, price, price, price, 1.0]
        for i, price in enumerate(closes)
    ]


# Crafted so SMA(2)/SMA(3) produces exactly one golden cross (BUY at index 3,
# price 20) followed by one death cross (SELL at index 6, price 10).
CROSSOVER_CLOSES = [10, 10, 10, 20, 20, 20, 10, 10, 10]


def test_run_backtest_executes_expected_trades_no_fees():
    candles = _candles_from_closes(CROSSOVER_CLOSES)
    config = BacktestConfig(
        short_window=2,
        long_window=3,
        starting_quote_balance=100.0,
        quote_amount_per_buy=50.0,
        fee_rate=0.0,
    )

    result = run_backtest(candles, config)

    assert len(result.trades) == 2
    buy, sell = result.trades
    assert buy.side == "buy" and buy.index == 3 and buy.price == 20
    assert buy.quote_value == pytest.approx(50.0)
    assert buy.base_amount == pytest.approx(2.5)

    assert sell.side == "sell" and sell.index == 6 and sell.price == 10
    assert sell.base_amount == pytest.approx(2.5)
    assert sell.quote_value == pytest.approx(25.0)  # 2.5 * 10, no fee

    assert result.final_quote_balance == pytest.approx(75.0)
    assert result.final_base_balance == pytest.approx(0.0)
    assert len(result.equity_curve) == len(candles)
    assert result.equity_curve[-1] == pytest.approx(75.0)


def test_run_backtest_applies_fees_on_both_sides():
    candles = _candles_from_closes(CROSSOVER_CLOSES)
    config = BacktestConfig(
        short_window=2,
        long_window=3,
        starting_quote_balance=100.0,
        quote_amount_per_buy=50.0,
        fee_rate=0.01,
    )

    result = run_backtest(candles, config)
    buy, sell = result.trades

    assert buy.base_amount == pytest.approx((50.0 * 0.99) / 20)
    assert sell.quote_value == pytest.approx(buy.base_amount * 10 * 0.99)


def test_run_backtest_buy_never_exceeds_available_cash():
    # starting balance smaller than quote_amount_per_buy: the buy should be
    # capped at whatever cash is actually available (no borrowing).
    candles = _candles_from_closes(CROSSOVER_CLOSES)
    config = BacktestConfig(
        short_window=2,
        long_window=3,
        starting_quote_balance=10.0,
        quote_amount_per_buy=50.0,
        fee_rate=0.0,
    )

    result = run_backtest(candles, config)
    buy = result.trades[0]
    assert buy.quote_value == pytest.approx(10.0)
    assert result.final_quote_balance >= 0


def test_run_backtest_sell_never_exceeds_held_base_balance():
    # A SELL signal with no prior BUY (e.g. data starts mid-downtrend) must
    # be a no-op — there is nothing held, and shorting is never simulated.
    closes = [20, 20, 20, 10, 10, 10]
    candles = _candles_from_closes(closes)
    config = BacktestConfig(short_window=2, long_window=3, starting_quote_balance=100.0)

    result = run_backtest(candles, config)
    assert all(trade.side != "sell" for trade in result.trades)
    assert result.final_base_balance == 0.0
    assert result.final_quote_balance == pytest.approx(100.0)


def test_run_backtest_requires_enough_candles():
    candles = _candles_from_closes([1, 2, 3])
    config = BacktestConfig(short_window=2, long_window=3)
    with pytest.raises(ValueError):
        run_backtest(candles, config)


def test_max_drawdown_pct():
    assert max_drawdown_pct([100, 120, 90, 150]) == pytest.approx(25.0)
    assert max_drawdown_pct([100, 110, 120]) == pytest.approx(0.0)


def test_summarize_reports_round_trip_win_rate():
    candles = _candles_from_closes(CROSSOVER_CLOSES)
    config = BacktestConfig(
        short_window=2, long_window=3, starting_quote_balance=100.0, quote_amount_per_buy=50.0, fee_rate=0.0
    )
    result = run_backtest(candles, config)
    stats = summarize(result, config)

    assert stats["num_trades"] == 2
    assert stats["num_round_trips"] == 1
    assert stats["win_rate_pct"] == pytest.approx(0.0)  # the round trip lost money
    assert stats["total_return_pct"] == pytest.approx(-25.0)
    assert stats["open_position_base"] == pytest.approx(0.0)


def test_backtestconfig_rejects_short_not_smaller_than_long():
    with pytest.raises(ValueError):
        BacktestConfig(short_window=30, long_window=10)


def test_backtestconfig_rejects_bad_fee_rate():
    with pytest.raises(ValueError):
        BacktestConfig(fee_rate=1.5)


def test_load_ohlcv_csv_round_trip(tmp_path):
    csv_path = tmp_path / "candles.csv"
    csv_path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2024-01-01T00:00:00Z,10,11,9,10,100\n"
        "1704067200000,10,11,9,11,100\n"  # same instant as row 1, epoch ms form
        "2024-01-01T02:00:00Z,11,12,10,12,100\n"
    )

    candles = load_ohlcv_csv(str(csv_path))

    assert len(candles) == 3
    # sorted by timestamp; the two rows sharing a timestamp keep file order
    assert candles[0][0] == candles[1][0] == 1704067200000
    assert candles[-1][4] == 12.0


def test_load_ohlcv_csv_rejects_missing_columns(tmp_path):
    csv_path = tmp_path / "bad.csv"
    csv_path.write_text("timestamp,open,close\n2024-01-01,10,11\n")
    with pytest.raises(ValueError):
        load_ohlcv_csv(str(csv_path))
