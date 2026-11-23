# Stabilised selective dip-buy cbBTC bot v2

[![make.fun bounty](https://make.fun/badge/btc-race-24h-20261123-0511.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511)
[![Rank](https://make.fun/badge/btc-race-24h-20261123-0511/sonipraboao/btc-dip-buyer-v2/rank.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511)
[![Reward](https://make.fun/badge/btc-race-24h-20261123-0511/sonipraboao/btc-dip-buyer-v2/reward.svg)](https://make.fun/bounty/btc-race-24h-20261123-0511)

A Python 3.13 bot for make.fun's [btc-race-24h-20261123-0511](https://make.fun/bounty/btc-race-24h-20261123-0511)
competition ("Race a BTC trading bot live for 24 hours"), speaking the
`agent-fun/bars-replay/v1` protocol. It trades one asset, CBBTC
(Coinbase Wrapped BTC on Solana, against USDC), on 1-minute bars with
1,000 USDC of paper money: long only, at most one open position.

## Strategy

Selective mean-reversion dip-buying with a regime filter — built on the
lesson of the previous race, where the winning bots made only a handful
of trades and won by sitting in cash, not by trading more:

- **Regime filter.** A 120-bar EMA of the close and its 60-bar slope.
  While the EMA is falling, no dip is bought: a downtrend is never
  chased.
- **Dip episode.** An episode opens when the close falls at least 0.4%
  below the EMA while the slope is non-negative. The bot then waits (at
  most 40 bars) for a *stabilised* rebound — two consecutive rising
  closes and a close at least 0.08% above the episode low — before it
  buys. The episode is abandoned if the price recovers to the EMA
  first, takes too long, or deepens past 1.8% below the EMA (a regime
  break, not a dip).
- **Position size.** Scaled by the episode's maximum depth: 60% of cash
  for a just-qualifying dip, up to a hard cap of 80% of cash for a deep
  one. Shallower than 0.4% never trades at all.
- **Exits (asymmetric, against a ~26–40 bps round-trip cost).**
  Take-profit at +1.0% over the average entry cost; a mean-reversion
  exit once the close is back at or above the EMA with at least +0.6%
  banked; a tight stop-loss at −0.7%; a time exit after 480 bars. After
  a sale the bot rests 20 bars.
- **No forced-buy fallback.** No trade beats a losing trade: the bot
  finishes in cash unless a dip genuinely stabilises.

The bot is deterministic: a pure function of the bars it has seen, with
no clock, no randomness, no files and no network, so two runs on the
same data produce byte-identical orders.

## Layout

- `main.py` — the bot (the entrypoint the grader runs)
- `vendor/makefun_bars.py` — the kit's Python SDK shim, copied unchanged
- `LICENSE` — MIT

## Running it

The grader runs `python3 -P -s main.py` and speaks newline-delimited
JSON on stdin/stdout: one `hello`, one `batch` per minute, then `done`.
The bot answers every batch with exactly one reply.
