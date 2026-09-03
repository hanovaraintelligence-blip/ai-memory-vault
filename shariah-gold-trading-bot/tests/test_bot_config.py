import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot import BotConfig  # noqa: E402


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
