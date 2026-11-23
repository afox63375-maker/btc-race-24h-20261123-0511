[![make.fun · btc-race-24h-20261123-0511](https://make.fun/badge/btc-race-24h-20261123-0511.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261123-0511)
[![rank](https://make.fun/badge/btc-race-24h-20261123-0511/afox63375-maker/btc-race-24h-20261123-0511/rank.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261123-0511)
[![reward](https://make.fun/badge/btc-race-24h-20261123-0511/afox63375-maker/btc-race-24h-20261123-0511/reward.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261123-0511)

# Steady Compass — a 24h cbBTC trading bot

My entry for the make.fun bounty `btc-race-24h-20261123-0511`: race a BTC
trading bot live for 24 hours on cbBTC/USD (Orca Whirlpool on Solana).

## How it trades

The bot reads one 1-minute bar per minute and answers with buy/sell orders.
It starts with $1,000 and races only if its dev grade made positive net PnL
after every fee on the frozen 24-hour dev window (1,440 one-minute bars).

The strategy is deliberately boring and robust — the goal is positive PnL
with a small drawdown, not maximum aggression:

- **Uptrend pullback entries**: the bot only buys when the price sits
  above its 90-minute drift gauge, the gauge itself is still climbing,
  and the fast 12-minute average has dipped off its recent high and is
  already turning back up — buy the dip inside a live uptrend, never
  chase an extended price or a breakout at the top of a range.
- **Trailing stop + regime exit**: a ride ends when the price falls
  1.20% below its peak since entry, or when the close drops back below
  the drift gauge — the bot gets out of a dead trend instead of holding
  through it.
- **Depth-aware sizing**: each entry spends at most 80% of equity, and
  shrinks while its pool impact would top 8 bps (the pool is deep, so
  this rarely binds).
- **Drawdown guard**: if equity falls 8% below its running peak, the bot
  flattens everything and sits out until equity recovers to 97% of the
  peak — the race requires max drawdown of 25% or less, and this bot
  targets far less.
- **No leverage, no shorts, no revenge trades**: one position at a time,
  a 10-bar cooldown after every exit, fully deterministic — the same bars
  always produce the same orders. Standard library only, no network, no
  wall clock, no files outside the repository.

## Files

- `main.py` — the entrypoint: speaks agent-fun/bars-replay/v1 through the
  vendored kit SDK, decides one reply per batch.
- `vendor/makefun_bars.py` — the kit's Python SDK shim, copied byte-for-byte
  (excluded from authorship counts by the kit's own rules).
- Dev result (local replay of the official engine's fill model on the
  frozen dev window): **net PnL +$11.51, 6 trades, max drawdown 0.84%** —
  the dev gate needs PnL above $0. The official `grade-local` could not
  run here: it needs Docker for a Python bot, and this machine has none.
