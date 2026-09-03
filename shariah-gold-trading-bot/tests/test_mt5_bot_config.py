import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# The real `MetaTrader5` package only installs on Windows (it talks to a
# local running terminal over IPC), so it can't be installed in this test
# environment. Stub it out before import — mt5_bot only touches mt5.*
# attributes inside function bodies, never at import time, so a bare stub
# is enough to validate the pure config/logic surface.
if "MetaTrader5" not in sys.modules:
    sys.modules["MetaTrader5"] = types.ModuleType("MetaTrader5")

from mt5_bot import MT5BotConfig  # noqa: E402


def test_mt5botconfig_rejects_short_window_not_smaller_than_long():
    try:
        MT5BotConfig(short_window=30, long_window=10)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_mt5botconfig_rejects_non_positive_volume():
    try:
        MT5BotConfig(volume=0)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_mt5botconfig_rejects_unknown_timeframe():
    try:
        MT5BotConfig(timeframe="H2")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


def test_mt5botconfig_defaults_are_valid():
    config = MT5BotConfig()
    assert config.symbol
    assert config.short_window < config.long_window
    assert config.volume > 0
    assert config.dry_run is True
