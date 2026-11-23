"""Steady Compass — a 24h cbBTC trading bot for make.fun's btc-race-24h.

Speaks agent-fun/bars-replay/v1 through the kit's Python shim (vendored
byte-for-byte under vendor/). One 1-minute bar per batch, long only, one
open position, $1,000 of paper money, fills at the next bar's open.

The idea, in one sentence: only buy when the slow drift is up AND the
price has just shaken out the weak hands — either a dip that is already
turning back up, or a push through the last hour's high — then ride it
with a trailing stop and get out the moment the drift itself turns.

Why this shape, instead of a plain average cross: a cross fires on every
little wiggle in a flat market, and each round trip costs about 26 bps in
fees, slippage and impact, so a choppy day bleeds the account dry. The dip
and the breakout both demand that the move first prove itself, and the
trailing stop caps what any single ride can give back.

Everything here is integer arithmetic on the protocol's own units (prices
at price_scale 5, money in micro-USDC), no floats, no randomness, no
clock, no files, no network: the same bars always produce the same
orders, byte for byte.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

# --- Tuning ---------------------------------------------------------------
SLOW_LEN = 90            # bars of the slow drift gauge (90 minutes)
FAST_LEN = 12            # bars of the fast average (12 minutes)
DIP_LOOKBACK = 24        # bars over which the fast average's high is taken
BREAKOUT_LEN = 60        # bars whose high a breakout must clear
TREND_GAP_BPS = 10       # close must sit this far above the slow gauge
EXTENSION_CAP_BPS = 50   # never chase: skip entries extended this far over the gauge
USE_BREAKOUT = 0         # 0: pullback entries only — breakouts bought range tops
TREND_ALIVE_BPS = 10     # slow gauge must have risen this far over the last hour
TREND_ALIVE_LOOKBACK = 60  # bars back to compare the slow gauge against
DIP_BPS = 15             # fast average must have dipped this far off its high
TRAIL_BPS = 35           # trailing stop: this far below the ride's peak
EXIT_GAP_BPS = 10        # regime flip: close this far below the slow gauge
EXIT_CONFIRM_BARS = 1    # bars the flip must persist before the exit fires
COOLDOWN_BARS = 10       # bars to wait after an exit before buying again
MIN_HOLD_BARS = 3        # never exit sooner than this after an entry
DD_HALT_BPS = 800        # flatten + halt when equity is 8% below its peak
DD_RESUME_BPS = 300      # resume only after equity recovers to 97% of peak
ENTRY_WEIGHT_BPS = 8000  # spend at most 80% of equity on one entry
MAX_IMPACT_BPS = 8       # shrink the entry while its pool impact tops this
MAX_SLIPPAGE_BPS = 150   # largest 1-minute move on the dev window was 72 bps
MIN_NOTIONAL = 1000000   # 1 USDC, in micro-USDC (the protocol's minimum)


def _mean(values):
    return sum(values) // len(values)


class Compass:
    """The bot's memory between batches. One instance per run."""

    def __init__(self):
        self.closes = []        # every close seen, newest last
        self.fast_hist = []     # every fast average, newest last
        self.peak_since_entry = None
        self.bars_held = 0
        self.flip_streak = 0
        self.cooldown = 0
        self.equity_peak = None
        self.halted = False
        self._was_holding = False

    # -- indicators ---------------------------------------------------------
    def _slow(self):
        return _mean(self.closes[-SLOW_LEN:])

    def _fast(self):
        return _mean(self.closes[-FAST_LEN:])

    def _fast_high(self):
        return max(self.fast_hist[-DIP_LOOKBACK:])

    def _hour_high(self):
        return max(self.closes[-BREAKOUT_LEN - 1:-1])

    # -- bookkeeping --------------------------------------------------------
    def _refresh_peak(self, equity):
        if self.equity_peak is None or equity > self.equity_peak:
            self.equity_peak = equity
        if self.halted and equity * 10000 >= self.equity_peak * (10000 - DD_RESUME_BPS):
            self.halted = False
        elif not self.halted and equity * 10000 < self.equity_peak * (10000 - DD_HALT_BPS):
            self.halted = True

    def _track_entry(self, holding, close):
        if holding and not self._was_holding:
            self.peak_since_entry = close
            self.bars_held = 1
        elif not holding:
            self.peak_since_entry = None
            self.bars_held = 0
        elif self.peak_since_entry is not None and close > self.peak_since_entry:
            self.peak_since_entry = close
            self.bars_held += 1
        else:
            self.bars_held += 1
        self._was_holding = holding

    # -- decision -----------------------------------------------------------
    def decide(self, state):
        """Return the orders for this batch (a list, usually empty or one)."""
        close = state.close(SYMBOL)
        if close is None:
            return []
        self.closes.append(close)
        self.fast_hist.append(self._fast())
        if self.cooldown > 0:
            self.cooldown -= 1

        holding = state.positions.get(SYMBOL)
        qty = holding.qty if holding is not None else 0
        self._track_entry(qty > 0, close)
        self._refresh_peak(state.equity)

        if self.halted and qty > 0:
            return [self._sell_all(qty)]
        if self.halted:
            return []

        if qty > 0:
            return self._maybe_exit(state, close, qty)
        return self._maybe_enter(state, close)

    def _maybe_exit(self, state, close, qty):
        if self.bars_held < MIN_HOLD_BARS:
            return []
        slow = self._slow()
        trail_hit = (self.peak_since_entry is not None
                     and close * 10000 < self.peak_since_entry * (10000 - TRAIL_BPS))
        flipped = close * 10000 < slow * (10000 - EXIT_GAP_BPS)
        self.flip_streak = self.flip_streak + 1 if flipped else 0
        if trail_hit or self.flip_streak >= EXIT_CONFIRM_BARS:
            self.cooldown = COOLDOWN_BARS
            self.flip_streak = 0
            return [self._sell_all(qty)]
        return []

    def _maybe_enter(self, state, close):
        if len(self.closes) < SLOW_LEN or self.cooldown > 0:
            return []
        slow = self._slow()
        fast = self._fast()
        uptrend = close * 10000 > slow * (10000 + TREND_GAP_BPS)
        if not uptrend:
            return []
        # Never chase an extended price: entries happen near the slow gauge,
        # where a dip has value, not at the top of a range.
        if close * 10000 > slow * (10000 + EXTENSION_CAP_BPS):
            return []
        # The drift itself must still be climbing: once the slow gauge goes
        # flat, the move is over and every new entry is just chop.
        if len(self.closes) >= SLOW_LEN + TREND_ALIVE_LOOKBACK:
            slow_then = _mean(self.closes[-SLOW_LEN - TREND_ALIVE_LOOKBACK:
                                         -TREND_ALIVE_LOOKBACK])
            if slow * 10000 < slow_then * (10000 + TREND_ALIVE_BPS):
                return []

        dipped = fast * 10000 < self._fast_high() * (10000 - DIP_BPS)
        turning = (len(self.fast_hist) >= 3
                   and self.fast_hist[-1] > self.fast_hist[-2] > self.fast_hist[-3])
        pullback = dipped and turning
        breakout = (USE_BREAKOUT and len(self.closes) > BREAKOUT_LEN
                    and close > self._hour_high())
        if not (pullback or breakout):
            return []

        notional = state.equity * ENTRY_WEIGHT_BPS // 10000
        notional = min(notional, state.cash, state.weight_room(SYMBOL))
        while notional > MIN_NOTIONAL:
            impact = state.impact_bps(SYMBOL, notional)
            if impact is None or impact <= MAX_IMPACT_BPS:
                break
            notional = notional * 3 // 4
        if notional < MIN_NOTIONAL:
            return []
        return [{"side": "buy", "symbol": SYMBOL,
                 "notional_quote_units": notional,
                 "max_slippage_bps": MAX_SLIPPAGE_BPS}]

    @staticmethod
    def _sell_all(qty):
        return {"side": "sell", "symbol": SYMBOL, "qty": qty,
                "max_slippage_bps": MAX_SLIPPAGE_BPS}


_COMPASS = Compass()


def on_batch(state, batch):
    """One reply per batch: warm-up batches never trade, the rest decide."""
    if state.warming_up:
        return []
    return _COMPASS.decide(state)


makefun_bars.run(on_batch)
