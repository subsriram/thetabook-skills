---
name: scan-watchlist
description: |
  Scan a list of tickers for an at-the-money options snapshot — spot price,
  near-DTE ATM IV, 25-delta skew, expected move, and an earnings-in-window
  flag — and rank them by a configurable composite score. A starting
  point for a watchlist sweep; thresholds are passed as flags so the
  skill stays generic and reproducible across users. Use when the user
  asks "what's interesting in my watchlist", "where's the rich vol",
  or wants a quick options-context table without drilling into a chain.
version: 0.1.0
tier: community
allowed-tools: [Bash]
reads: [personal/snapshots, personal/watchlist.toml]
writes: []
network: [schwab, yfinance]
tags: [analysis, watchlist]
when-to-use: |
  User wants a one-pass options-context snapshot of multiple tickers
  with no on-disk state. For a tuned variant with IV rank / history /
  strategy decision tree, see the personal `scan.local` template
  upstream in thetabook (gitignored, opinionated, single-user).
examples:
  - "scan AAPL NVDA SPY"
  - "scan my watchlist for skewed-put names"
---

# scan-watchlist

A read-only, stateless watchlist scanner. For each ticker:

- Fetches spot via `thetabook price`.
- Picks the nearest expiry at or beyond `--dte` (default 30 days).
- Fetches the chain with Greeks via `thetabook chain --json --greeks`.
- Computes ATM IV, 25Δ put/call skew, ATM bid-ask spread %, expected move (from ATM straddle).
- Checks earnings calendar via `thetabook earnings`; flags an event in the trade window.
- Ranks rows by `(IV × W_IV + tightness × W_LIQ)` with an optional earnings penalty.

This skill writes nothing. It does NOT compute IV rank — that requires per-ticker history, which is a stateful feature. For an opinionated tuned scanner with history, decision tree, and score formula, see `scan.local` (the personal-tier slash command — gitignored on the consuming repo).

## How to invoke

When `/scan-watchlist` runs, execute the helper via the Bash tool:

```
~/.local/bin/uv run python "$CLAUDE_PROJECT_DIR/.claude/commands/scan-watchlist/scan.py" $ARGUMENTS
```

If `$CLAUDE_PROJECT_DIR` isn't set, substitute the absolute path to the project root.

Pass the helper's stdout to the user as-is. If stderr contains anything beyond `Scanning N tickers...`, surface it as bullet points.

## CLI

```
scan.py [TICKERS...] [--watchlist] [--dte N] [--w-iv F] [--w-liq F] [--earnings-penalty F]
```

- `[TICKERS...]` — positional; tickers to scan.
- `--watchlist` — also include tickers from `personal/watchlist.toml` `[watchlist] tickers`.
- `--dte N` — target days-to-expiry for the near-leg pick. Default 30.
- `--w-iv F` — composite score weight on ATM IV. Default 1.0.
- `--w-liq F` — composite score weight on liquidity (1 - spread%). Default 0.5.
- `--earnings-penalty F` — score multiplier shaved when earnings fall in the trade window. Default 0.4.

## Notes

- All thresholds are flags; the skill ships no opinionated defaults besides the score weights above. To bake in your own constants, install your tuned override as a `.local` skill upstream.
- The skill never places, modifies, or cancels trades — analysis only.
- Output is plain text designed to be readable without color. Prefix glyphs (`⚠`, `✓`) are intentional.
