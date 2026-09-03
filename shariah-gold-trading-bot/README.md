# Shariah-Compliant Gold Trading Bot

A simple SMA (moving-average) crossover bot for **spot** gold trading, written to
stay within the constraints most scholars apply to permissible (halal) trading:

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

## Choosing a symbol

Use a **spot-settled** gold instrument your exchange actually lists, for
example tokenized gold like `PAXG/USDT` or `XAUT/USDT`. Do not point this at
a gold CFD, future, or perpetual swap symbol — `build_exchange()` checks the
market metadata and refuses to trade anything that isn't a spot market.

## Tests

```bash
pip install pytest
pytest tests/
```

The tests cover the SMA calculation and crossover-signal logic; they don't
touch any exchange or network.

## Disclaimer

Trading involves risk of loss. This script is provided for educational
purposes, is not financial advice, and is not a substitute for independent
Shariah review of your specific broker, instrument, and jurisdiction.
