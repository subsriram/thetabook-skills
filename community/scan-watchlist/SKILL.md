---
name: scan-watchlist
description: |
  Sweep a watchlist of tickers and rank by options-trade opportunity.
  Computes per-ticker ATM IV, IV rank (vs local history), 25Δ skew,
  term-structure slope, expected move, ATM liquidity, earnings-in-window,
  ex-div-in-window, and current holdings; applies a strategy decision
  tree; ranks by a composite score. All thresholds are flags so each
  user can tune without forking. Use when the user asks "what's
  interesting in my watchlist", "where's the rich vol", or wants a
  one-pass options-context table.
version: 0.2.1
tier: community
allowed-tools: [Bash]
reads: [personal/snapshots, personal/watchlist.toml, personal/iv_history]
writes: [personal/iv_history, personal/watchlist.toml]
network: [schwab, yfinance]
tags: [analysis, watchlist]
when-to-use: |
  User wants a ranked, analyzed options-context table across a
  watchlist — IV rank vs history, suggested strategy per ticker, and
  per-row notes (earnings/ex-div in window, holdings context, history
  thinness). Defaults are starting heuristics; pass flags to tune.
examples:
  - "scan AAPL NVDA SPY"
  - "scan my watchlist for short-put names"
  - "scan --watchlist --iv-rank-high 0.7 --skew-put-rich 0.08"
---

# scan-watchlist

For each ticker, fetches near + far chains, computes the vol-surface metrics, appends today's ATM IV to a per-ticker history (`personal/iv_history/<TICKER>.jsonl`, idempotent per date), derives an IV rank from that history, applies a strategy decision tree (vol regime + skew + term slope + holdings + event risk), and renders a ranked table with a `Suggested Strategy` column and a per-row `Notes` block.

**IV rank starts empty.** Until the history file has at least `--min-history-days` entries (default 10), the rank reads `INSUF` and the strategy reads `watch — building IV history (n/N days)`. That's correct, not a bug — run the skill daily and the rank fills in.

**Suggested strategies are advisory.** This skill never places trades. Pair with `/accounts check-idea` and a chain drill-down before acting.

## How to invoke

When `/scan-watchlist` runs, execute the helper via the Bash tool:

```
~/.local/bin/uv run python "${CLAUDE_PROJECT_DIR:-$PWD}/.claude/skills/scan-watchlist/scan.py" $ARGUMENTS
```

The `${CLAUDE_PROJECT_DIR:-$PWD}` form uses the harness-set env var when present and falls back to the current directory otherwise — covers both the standard slash-command flow and direct Bash-tool invocation from the project root. Print stdout to the user as-is. If stderr contains anything beyond `Scanning N tickers × 2 expiries...` and per-ticker `chain fetch failed: …` lines, surface it as bullet points.

After the helper exits, offer next steps: *"Want to dig into any of these? `/chain <T>` to drill into a chain, `/idea` to draft a trade, or `/evaluate <id>` if one maps to an existing idea."*

## CLI

```
scan.py [TICKERS...] [flags]
```

### Selection

- `[TICKERS...]` — positional; tickers to scan (uppercased automatically).
- `--watchlist` — also include tickers from `personal/watchlist.toml` `[watchlist] tickers`. Implicit when no positional tickers are given.
- `--personal-dir PATH` — override personal/ resolution. Default cascade: `--personal-dir` → `thetabook snapshot --path` → `cwd/personal`.
- `--add-to-watchlist` — append positional tickers (not already present) to `[watchlist] tickers` in `personal/watchlist.toml`. Top-level comments are dropped on rewrite; `[notes]` keys/values are preserved.

### Expiry pick

- `--expiry YYYY-MM-DD` — pin the near-leg expiry; overrides `--dte`.
- `--dte N` — near-leg target DTE (default 30). Picks the soonest expiry at or beyond N.
- `--term-dte N` — far-leg target DTE for term-structure slope (default 60).

### Decision-tree thresholds (defaults are starting heuristics)

- `--iv-rank-high F` — IV rank ≥ this is "elevated, sell premium" (default 0.60).
- `--iv-rank-low F` — IV rank ≤ this is "depressed, buy premium" (default 0.30).
- `--skew-put-rich F` — 25Δ put IV − 25Δ call IV ≥ this (vol points) is "put-rich" (default 0.05).
- `--term-slope-strong F` — `|term_slope| ≥ this` triggers calendar branches (default 0.02).
- `--min-history-days N` — below this, IV rank reads `INSUFFICIENT` (default 10).

### Score weights

Composite score = `(w_rank × rank + w_liquidity × liquidity) × earnings_factor`.

- `--w-rank F` — weight on IV rank (default 1.0).
- `--w-liquidity F` — weight on liquidity score (default 0.5).
- `--earnings-penalty F` — multiplier shaved when earnings fall in the trade window (default 0.40).

### Liquidity benchmarks

- `--liquidity-good-oi N` — ATM open interest at/above this scores 1.0 (default 500).
- `--liquidity-good-spread F` — ATM spread/mid at/below this scores 1.0 (default 0.05).

### Filters

- `--min-iv F` — drop rows below this ATM IV percent (e.g. `--min-iv 30` keeps IV ≥ 30%).
- `--strategy SUB` — case-insensitive substring filter on the suggested strategy column.

## Output columns

| Col | Meaning |
|---|---|
| Ticker / Spot / ATM IV | spot + near-DTE ATM IV |
| Rank | IV rank from local history (`INSUF` until `--min-history-days` reached) |
| Skew | 25Δ put IV − 25Δ call IV, in vol points |
| Term | far-DTE ATM IV − near-DTE ATM IV, in vol points |
| Liq | composite liquidity score (OI + spread tightness), 0–1 |
| Score | composite ranking score |
| Earn / ExDiv | ✓ if event falls inside the near-expiry window |
| ExpMv | expected move from ATM straddle (or BS approximation if missing) |
| Held | `shrs` / `opts` / `S+O` / `—` from latest snapshot |
| Suggested Strategy | decision-tree output |

A `Notes:` block follows the table with per-ticker context: history-thin warnings, earnings/ex-div proximity, watchlist `[notes]` annotations, and missing-data flags.

## Notes

- The skill writes only to `personal/iv_history/` (idempotent JSONL append per ticker × date) and, when `--add-to-watchlist` is passed, `personal/watchlist.toml`. Both paths are user-owned per `RESERVED_WRITE_PATHS` policy.
- `held_as_shares` uses `SHARE_ASSET_TYPES = {EQUITY, ETF, COLLECTIVE_INVESTMENT, MUTUAL_FUND}` — strict prerequisite for the covered-call / diagonal branches, since you can't write a CC against a long call.
- Concurrency: `ThreadPoolExecutor` with 4 workers for chain fetches. Drop to 1 if Schwab rate-limits.
- `--add-to-watchlist` always appends positional tickers, even if their chain fetch failed — failures are often transient and the user explicitly opted in.
- For a personal-tuned variant where thresholds live as constants in code (cleaner edit surface than long flag chains), copy this skill to `.claude/commands/scan.local/SKILL.md` + `scan.py` and inline the constants. `.local` skills bypass the registry lint.
