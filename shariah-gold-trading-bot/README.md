# Shariah-Compliant Gold Trading Bot

A simple SMA (moving-average) crossover strategy for gold trading, written to
stay within the constraints most scholars apply to permissible (halal) trading.
The strategy logic ([`strategy.py`](strategy.py)) is shared by two live-trading
scripts:

- **[`bot.py`](bot.py)** — spot gold on a crypto exchange via `ccxt` (e.g.
  tokenized gold like `PAXG/USDT`). This is the compliant-by-construction
  version: it only ever trades a real spot market.
- **[`mt5_bot.py`](mt5_bot.py)** — the same strategy on MetaTrader 5. Read the
  [MetaTrader 5](#metatrader-5) section below **before** using this one — on
  most brokers, MT5 "XAUUSD" is a CFD, not spot gold, which changes the
  compliance picture significantly.

Plus [`backtest.py`](backtest.py) to replay the strategy against historical
OHLCV data before risking anything live.

`bot.py`'s constraints, spelled out:

- **Spot only** — no margin, no leverage, no borrowing to trade (avoids riba).
- **No short-selling** — the bot only ever goes long, then flat. A SELL signal
  never sells more than the base asset actually held in the spot wallet, so a
  negative (borrowed) position is never possible.
- **No overnight swap/rollover fees** — pair this with an "Islamic" / swap-free
  spot account, or an instrument that settles immediately (spot, not
  CFDs/futures). The bot itself never opens a margin or futures position.
- **Simple, transparent strategy** — a plain SMA crossover, not an opaque
  derivative, to keep gharar (excessive uncertainty) low.

See the compliance notes at the top of [`bot.py`](bot.py) for the full detail.
**This is not a fatwa and not financial advice.** Confirm compliance with a
qualified Islamic finance scholar or a shariah-certified broker, for your
specific broker/instrument/jurisdiction, before trading real funds.

## Strategy

- Compute a short-window and a long-window SMA of closing prices.
- **BUY** (spot market order) when the short SMA crosses above the long SMA.
- **SELL** (spot market order, sell-to-flat) when the short SMA crosses below
  the long SMA.
- The bot never requests leverage and never sells more than it holds.

## Setup

```bash
cd shariah-gold-trading-bot
pip install -r requirements.txt
cp config.example.env .env
# edit .env: exchange, API key/secret (spot-trading permission only), symbol, sizing
```

Load `.env` into your shell however you prefer (e.g. `export $(cat .env | xargs)`,
or a tool like `python-dotenv`/`direnv`), then run:

```bash
# Dry run (default) — logs signals and simulated orders, sends nothing to the exchange
python bot.py

# Evaluate the signal once and exit, instead of looping
python bot.py --once

# Send real spot orders (only after you've reviewed the strategy and config)
python bot.py --live
```

`DRY_RUN=true` is the default in `config.example.env` on purpose. Flip it to
`false` (or pass `--live`) only once you understand and accept the strategy
and its risks.

## Backtesting

Before running the bot live, replay the strategy against historical OHLCV
data with `backtest.py`. It reuses the exact same SMA/crossover functions as
`bot.py`, so the result reflects what the live bot would actually have done.
It never places any orders — it only reads historical price data.

```bash
# Against a local OHLCV CSV (timestamp,open,high,low,close,volume — timestamp
# can be epoch milliseconds or an ISO 8601 / YYYY-MM-DD date)
python backtest.py --csv path/to/candles.csv \
  --short-window 10 --long-window 30 \
  --starting-balance 1000 --quote-amount-per-buy 100 --fee-rate 0.001

# Or fetch historical candles live via ccxt (spot markets only)
python backtest.py --exchange binance --symbol PAXG/USDT --timeframe 1h \
  --since 2024-01-01 --until 2024-06-01
```

This prints total return, a buy-and-hold comparison, trade count, win rate,
and max drawdown, and can optionally write the full equity curve to CSV with
`--csv-out equity.csv`. The simulation is cash-based (starts with a
quote-currency balance; buys spend cash it has, sells liquidate only the
base balance actually held), so — like the live bot — there is nothing to
simulate that would require leverage or shorting.

Backtested performance on historical data is not a guarantee of future
results.

## Choosing a symbol

Use a **spot-settled** gold instrument your exchange actually lists, for
example tokenized gold like `PAXG/USDT` or `XAUT/USDT`. Do not point this at
a gold CFD, future, or perpetual swap symbol — `build_exchange()` checks the
market metadata and refuses to trade anything that isn't a spot market.

## MetaTrader 5

`mt5_bot.py` runs the identical crossover strategy on MetaTrader 5. **Read
this section fully before using it**, even on a demo account — the
compliance story is different from `bot.py`.

### Why MT5 changes the compliance picture

`bot.py` is compliant-by-construction because it only ever touches a real
spot market: it checks `market['spot'] is True` and refuses anything else.
MT5 doesn't have an equivalent concept for most retail brokers' gold
offering: **"XAUUSD" on MT5 is almost always a CFD** (a leveraged derivative
contract with the broker), not a real, ownable, spot-settled asset. Most
scholars do not consider a standard CFD Shariah-compliant regardless of
whether swap fees are removed, because:

- you never own or take delivery of the underlying gold, and
- the contract itself resembles a wagering/derivative instrument (gharar).

`mt5_bot.py` checks the symbol's `swap_long`/`swap_short` rates at startup
and before every order, and **refuses to trade any symbol that charges a
nonzero overnight swap** — so at minimum it can't cause an interest-like
charge to accrue through it. That removes one problem (riba from swap fees),
not the underlying-ownership problem. Unless your broker offers a genuinely
different, physically-backed / spot-settled gold product on MT5 (ask them
directly — plain "XAUUSD" almost never qualifies), treat `mt5_bot.py` as a
**strategy/signal-testing tool**, not a compliant live-trading tool, without
independent confirmation from a qualified scholar or shariah-certified
broker for your specific account and instrument.

Also note: MT5 accounts carry a broker-set, account-wide leverage ratio
(e.g. 1:100) that this script cannot turn off order-by-order — unlike
`bot.py`, which is leverage-free simply because it never touches margin.
Ask your broker for a 1:1 account if one is available.

### Why this can't run in a cloud/CLI session

The `MetaTrader5` Python package talks to a **running MT5 terminal on the
same machine** over local IPC — it is not a network API, doesn't install on
Linux (Windows only, or Windows under Wine), and needs a GUI terminal
already logged into your account. It cannot run headless in a remote/cloud
environment. You'll need to run it on your own Windows machine (a VM works).

### Demo-account walkthrough

1. Download and install the **MetaTrader 5** terminal from your broker (most
   retail FX/CFD brokers offer it — pick one that explicitly offers an
   "Islamic" / swap-free account option, since `mt5_bot.py` requires zero
   swap on the symbol it trades).
2. In the terminal: **File > Open an Account** → choose your broker's server
   → select **Demo Account** → fill in the form. This gives you a free demo
   login (account number + password) with fake funds, no real money.
3. **Tools > Options > Expert Advisors** → check **"Allow algorithmic
   trading"** (and "Allow DLL imports" if prompted). Without this, MT5 blocks
   all order requests from scripts.
4. Add the gold symbol to Market Watch (right-click Market Watch → Symbols →
   find `XAUUSD` or your broker's equivalent → Show).
5. On the machine running the terminal:
   ```powershell
   pip install MetaTrader5
   cd shariah-gold-trading-bot
   copy config.mt5.example.env .env.mt5
   # edit .env.mt5: confirm the symbol, timeframe, lot size
   ```
   Load `.env.mt5` into your environment however you prefer, then:
   ```powershell
   python mt5_bot.py            # dry run — logs signals, sends nothing
   python mt5_bot.py --once     # evaluate the signal once and exit
   python mt5_bot.py --live     # send real orders (demo account funds only, to start)
   ```
6. Watch the log output and the terminal's **Trade** tab. Confirm every BUY
   is a plain market order with no margin call implications you don't
   understand, and that the symbol truly shows zero swap in **Market Watch →
   right-click symbol → Specification**.

Stay on the demo account until you've watched the strategy behave the way
you expect across at least a few crossovers, and until you've gotten
independent Shariah sign-off on your specific broker/instrument if you
intend to go further.

## Tests

```bash
pip install pytest
pytest tests/
```

The tests cover the SMA calculation, crossover-signal logic, the backtest
simulation (trade execution, fees, drawdown, CSV loading), and the MT5 bot's
config validation; they don't touch any exchange, broker terminal, or
network. The MT5 tests stub out the `MetaTrader5` package (it only installs
on Windows) since `mt5_bot.py` never touches its attributes at import time —
only the pure config/logic surface is exercised without a real terminal.

## Disclaimer

Trading involves risk of loss. This script is provided for educational
purposes, is not financial advice, and is not a substitute for independent
Shariah review of your specific broker, instrument, and jurisdiction.
