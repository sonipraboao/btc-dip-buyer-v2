"""Stabilised selective dip-buy cbBTC bot v2.

A Python 3.13 bot for the agent-fun/bars-replay/v1 protocol. It trades one
asset, CBBTC (Coinbase Wrapped BTC on Solana, against USDC), on 1-minute
bars with 1,000 USDC of paper money.

Strategy (written from scratch for this competition):

- Trend: keep a 120-bar EMA of the close and its 60-bar slope. The slope
  is the regime filter: while the EMA is falling, no dip is bought, so a
  downtrend is never chased the way a plain mean-reversion bot chases it.
- Entry: a dip episode opens when the close falls at least 0.4% below the
  EMA while the EMA slope is non-negative. The bot then waits inside the
  episode (at most 40 bars) for a stabilised rebound: two consecutive
  rising closes and a close at least 0.08% above the episode low. An
  episode is abandoned if the price recovers to the EMA first, runs past
  40 bars, or deepens past 1.8% below the EMA (a regime break, not a dip).
- Size: scaled by the episode's maximum depth, from 60% of cash for a
  just-qualifying dip up to a hard cap of 80% of cash for a deep one.
  Shallower than 0.4% never trades at all.
- Exit (asymmetric, sized against the ~26-40 bps round-trip cost):
  take-profit at +1.0% over the average entry cost, a mean-reversion
  exit once the close is back at or above the EMA with at least +0.6%
  profit, a tight stop-loss at -0.7%, and a time exit after 480 bars.
  After a sale the bot rests 20 bars before it may enter again.
- There is deliberately no forced-buy fallback: no trade beats a losing
  trade. The bot sits in cash unless a dip genuinely stabilises.

Deterministic: a pure function of the hello and the bars seen, with no
clock, no randomness, no environment, no files and no network, so two
runs on the same data produce byte-identical order logs.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

SYMBOL = "CBBTC"

EMA_PERIOD = 120          # bars in the trend EMA (2 hours)
SLOPE_LOOKBACK = 60       # bars over which the EMA slope is measured
DIP_THRESHOLD = 0.004     # dip episode opens 0.4% below the EMA
EPISODE_MAX_BARS = 40     # a dip that takes longer is not stabilising
EPISODE_MAX_DEPTH = 0.018  # deeper than 1.8% below the EMA is a break
BOUNCE = 0.0008           # rebound must lift the close 0.08% off the low
RISES_NEEDED = 2          # consecutive rising closes confirming the turn
BASE_FRACTION = 0.40      # position scale: fraction = base + depth * gain
DEPTH_GAIN = 50.0
MAX_FRACTION = 0.80       # never expose more than 80% of cash
TAKE_PROFIT = 0.010       # +1.0% over average entry cost
MEAN_EXIT_MIN = 0.006     # at/above the EMA, bank at least +0.6%
STOP_LOSS = 0.007         # -0.7% under average entry cost
MAX_HOLD_BARS = 480       # time exit
COOLDOWN_BARS = 20        # rest after a sale
MAX_SLIPPAGE_BPS = 250

_closes = []              # every close seen, as floats (price-scale units)
_ema = []                 # EMA of the closes, same length
_rises = 0                # current run of consecutive rising closes
_in_episode = False
_episode_low = 0.0
_episode_depth = 0.0
_episode_start = -1
_entry_bar = -1           # batch index at which the open position was seen
_last_sale_bar = -10 ** 9
_order_pending = False    # an order was sent on the previous batch


def _reset():
    global _closes, _ema, _rises, _in_episode, _episode_low
    global _episode_depth, _episode_start, _entry_bar, _last_sale_bar
    global _order_pending
    _closes = []
    _ema = []
    _rises = 0
    _in_episode = False
    _episode_low = 0.0
    _episode_depth = 0.0
    _episode_start = -1
    _entry_bar = -1
    _last_sale_bar = -10 ** 9
    _order_pending = False


def on_hello(state):
    _reset()


def _slope():
    if len(_ema) <= SLOPE_LOOKBACK:
        return 0.0
    return _ema[-1] / _ema[-1 - SLOPE_LOOKBACK] - 1.0


def on_batch(state, batch):
    global _rises, _in_episode, _episode_low, _episode_depth
    global _episode_start, _entry_bar, _last_sale_bar, _order_pending

    bar = None
    for b in batch["bars"]:
        if b["symbol"] == SYMBOL:
            bar = b
    if bar is None:
        return []

    close = int(bar["c"])
    prev_close = _closes[-1] if _closes else None
    _closes.append(close)
    if prev_close is None or len(_ema) == 0:
        _ema.append(float(close))
    else:
        k = 2.0 / (EMA_PERIOD + 1)
        _ema.append(close * k + _ema[-1] * (1.0 - k))
    _rises = _rises + 1 if prev_close is not None and close > prev_close else 0

    idx = len(_closes) - 1
    was_pending = _order_pending
    _order_pending = False

    holding = state.positions.get(SYMBOL)
    if state.warming_up:
        return []
    if idx < EMA_PERIOD:
        return []

    ema_now = _ema[-1]

    if holding is not None and holding.qty > 0:
        if _entry_bar < 0:
            _entry_bar = idx
        # Average entry price in the same raw units as the close: the
        # position cost is in quote units (scale 6) for a quantity at
        # scale 8 priced at scale 5, so price = cost * 10**7 / qty.
        entry_price = holding.cost * 10_000_000 / holding.qty
        ret = close / entry_price - 1.0
        if (
            ret >= TAKE_PROFIT
            or ret <= -STOP_LOSS
            or idx - _entry_bar >= MAX_HOLD_BARS
            or (close >= ema_now and ret >= MEAN_EXIT_MIN)
        ):
            _order_pending = True
            _last_sale_bar = idx
            _entry_bar = -1
            return [
                {
                    "side": "sell",
                    "symbol": SYMBOL,
                    "qty": holding.qty,
                    "max_slippage_bps": MAX_SLIPPAGE_BPS,
                }
            ]
        return []

    _entry_bar = -1
    if was_pending:
        # A buy was sent last batch but no position is open (rejected or
        # still in flight): do not stack another decision on top of it.
        return []
    if idx - _last_sale_bar < COOLDOWN_BARS:
        return []

    deviation = close / ema_now - 1.0
    slope = _slope()

    if not _in_episode:
        if deviation <= -DIP_THRESHOLD and slope >= 0.0:
            _in_episode = True
            _episode_low = float(close)
            _episode_depth = -deviation
            _episode_start = idx
        return []

    # Inside a dip episode: track it, abandon it, or buy its rebound.
    if close < _episode_low:
        _episode_low = float(close)
    if -deviation > _episode_depth:
        _episode_depth = -deviation
    if (
        close >= ema_now
        or idx - _episode_start > EPISODE_MAX_BARS
        or _episode_depth > EPISODE_MAX_DEPTH
    ):
        _in_episode = False
        return []
    if (
        _rises >= RISES_NEEDED
        and close >= _episode_low * (1.0 + BOUNCE)
        and slope >= 0.0
    ):
        fraction = BASE_FRACTION + _episode_depth * DEPTH_GAIN
        if fraction > MAX_FRACTION:
            fraction = MAX_FRACTION
        spend = int(state.cash * fraction)
        _in_episode = False
        if spend < 1_000_000:  # below the 1 USDC minimum notional
            return []
        _order_pending = True
        return [
            {
                "side": "buy",
                "symbol": SYMBOL,
                "notional_quote_units": spend,
                "max_slippage_bps": MAX_SLIPPAGE_BPS,
            }
        ]
    return []


if __name__ == "__main__":
    makefun_bars.run(on_batch, on_hello=on_hello)
