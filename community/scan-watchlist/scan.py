"""scan-watchlist helper — stateless options-context scanner.

A community-tier starter that ranks a watchlist of tickers by a simple
composite score over near-DTE ATM IV + liquidity (1 - spread%) with an
earnings-in-window penalty. No on-disk state; thresholds are flags.

For an opinionated tuned variant with IV rank / history / strategy
decision tree, see the upstream `scan.local` template in thetabook.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

UV = os.path.expanduser("~/.local/bin/uv")


# ---------------------------------------------------------------------------
# thetabook CLI shims
# ---------------------------------------------------------------------------


def cli(*args: str, check: bool = True) -> str:
    out = subprocess.run(
        [UV, "run", "thetabook", *args],
        capture_output=True,
        text=True,
        check=check,
    )
    return out.stdout


def resolve_personal_dir() -> Path:
    """personal/ via `thetabook snapshot --path` (worktree fallback included)."""
    s = cli("snapshot", "--path", check=False).strip()
    if not s:
        return Path.cwd() / "personal"
    snap = Path(s)
    return snap.parent.parent if snap.exists() else Path.cwd() / "personal"


def load_watchlist(personal: Path) -> list[str]:
    """Read `[watchlist] tickers` from personal/watchlist.toml; empty if absent."""
    f = personal / "watchlist.toml"
    if not f.is_file():
        return []
    import tomllib

    with f.open("rb") as fp:
        data = tomllib.load(fp)
    return list(data.get("watchlist", {}).get("tickers", []))


def fetch_expirations(ticker: str) -> list[str]:
    return json.loads(cli("chain", ticker, "--json")).get("expirations", [])


def pick_expiry(expirations: list[str], target_dte: int) -> tuple[str, int]:
    today_d = date.today()
    candidates = [
        ((date.fromisoformat(e) - today_d).days, e)
        for e in expirations
        if (date.fromisoformat(e) - today_d).days >= target_dte
    ]
    if not candidates:
        # No expiry at-or-beyond target — fall back to the longest available.
        candidates = [((date.fromisoformat(e) - today_d).days, e) for e in expirations]
        candidates.sort(reverse=True)
        return candidates[0][1], candidates[0][0]
    candidates.sort()
    return candidates[0][1], candidates[0][0]


def fetch_chain(ticker: str, expiry: str) -> list[dict]:
    return json.loads(cli("chain", ticker, expiry, "--json", "--greeks"))


def fetch_prices(tickers: list[str]) -> dict[str, float]:
    out = cli("price", *tickers, check=False)
    spots: dict[str, float] = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] in tickers and parts[1] == "$":
            try:
                spots[parts[0]] = float(parts[2].replace(",", ""))
            except ValueError:
                pass
    return spots


def fetch_earnings(tickers: list[str]) -> dict[str, tuple[str | None, int | None]]:
    out = cli("earnings", *tickers, check=False)
    result: dict[str, tuple[str | None, int | None]] = {t: (None, None) for t in tickers}
    for line in out.splitlines():
        parts = line.split()
        if not parts or parts[0] not in tickers:
            continue
        if "—" in line or "unknown" in line.lower():
            continue
        try:
            d = date.fromisoformat(parts[1])
            result[parts[0]] = (parts[1], (d - date.today()).days)
        except (ValueError, IndexError):
            pass
    return result


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def derive_metrics(chain: list[dict], spot: float, dte: int) -> dict:
    """Compute ATM IV, 25Δ skew, ATM spread %, expected move from one chain."""
    calls = [c for c in chain if c.get("type") == "CALL"]
    puts = [c for c in chain if c.get("type") == "PUT"]
    if not calls or not puts:
        return {}
    strikes = sorted({c["strike"] for c in chain})
    atm_strike = min(strikes, key=lambda s: abs(s - spot))
    atm_call = next((c for c in calls if c["strike"] == atm_strike), None)
    atm_put = next((p for p in puts if p["strike"] == atm_strike), None)

    ivs = [x["implied_vol"] for x in (atm_call, atm_put) if x and x.get("implied_vol")]
    atm_iv = sum(ivs) / len(ivs) if ivs else None

    def mid(c: dict | None) -> float | None:
        if not c:
            return None
        b, a = c.get("bid", 0), c.get("ask", 0)
        return (b + a) / 2 if a > b > 0 else None

    atm_call_mid, atm_put_mid = mid(atm_call), mid(atm_put)

    spreads = []
    for c in (atm_call, atm_put):
        m = mid(c)
        if c and m and (c.get("ask", 0) - c.get("bid", 0)) > 0:
            spreads.append((c["ask"] - c["bid"]) / m)
    atm_spread_pct = sum(spreads) / len(spreads) if spreads else None

    valid_puts = [p for p in puts if p.get("delta") is not None and p.get("implied_vol")]
    valid_calls = [c for c in calls if c.get("delta") is not None and c.get("implied_vol")]
    p25 = min(valid_puts, key=lambda p: abs(p["delta"] + 0.25), default=None)
    c25 = min(valid_calls, key=lambda c: abs(c["delta"] - 0.25), default=None)
    iv_p25 = p25["implied_vol"] if p25 else None
    iv_c25 = c25["implied_vol"] if c25 else None
    skew = (iv_p25 - iv_c25) if (iv_p25 and iv_c25) else None

    if atm_call_mid and atm_put_mid:
        exp_move_pct = (atm_call_mid + atm_put_mid) / spot
    elif atm_iv:
        exp_move_pct = atm_iv * math.sqrt(dte / 365)
    else:
        exp_move_pct = None

    return {
        "atm_strike": atm_strike,
        "atm_iv": atm_iv,
        "atm_spread_pct": atm_spread_pct,
        "skew": skew,
        "exp_move_pct": exp_move_pct,
    }


def compute_score(
    atm_iv: float | None,
    spread_pct: float | None,
    earnings_in_window: bool,
    *,
    w_iv: float,
    w_liq: float,
    earnings_penalty: float,
) -> float:
    if atm_iv is None:
        return 0.0
    liq = max(0.0, 1.0 - (spread_pct or 1.0))
    base = w_iv * atm_iv + w_liq * liq
    return base * ((1.0 - earnings_penalty) if earnings_in_window else 1.0)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def fmt_pct(v: float | None, digits: int = 0) -> str:
    return f"{v * 100:.{digits}f}%" if v is not None else "—"


def fmt_signed_vp(v: float | None) -> str:
    return f"{v * 100:+.1f}vp" if v is not None else "—"


def render(rows: list[dict], expiry: str, dte: int) -> None:
    rows.sort(key=lambda r: r["score"], reverse=True)
    print()
    print(f"Watchlist scan — near {expiry} ({dte} DTE), {len(rows)} tickers")
    print()
    header = (
        f"{'Ticker':<6}  {'Spot':>9}  {'ATM IV':>6}  {'Skew':>7}  "
        f"{'Spread':>6}  {'ExpMv':>6}  {'Earn':>4}  {'Score':>5}"
    )
    print(header)
    print("-" * len(header))
    for r in rows:
        em = f"±{r['exp_move_pct'] * 100:.1f}%" if r["exp_move_pct"] is not None else "—"
        print(
            f"{r['ticker']:<6}  ${r['spot']:>8.2f}  {fmt_pct(r['atm_iv']):>6}  "
            f"{fmt_signed_vp(r['skew']):>7}  {fmt_pct(r['spread'], 1):>6}  "
            f"{em:>6}  {('⚠' if r['earn_in_window'] else '—'):>4}  {r['score']:>5.2f}"
        )
    print()
    print("Legend: vp = vol points. Skew = 25Δ put IV − 25Δ call IV. ⚠ = earnings inside trade window.")
    print("Score = (W_IV × ATM IV + W_LIQ × (1 − spread%)) × earnings_factor.")


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


def main() -> None:
    p = argparse.ArgumentParser(prog="scan-watchlist")
    p.add_argument("tickers", nargs="*")
    p.add_argument("--watchlist", action="store_true")
    p.add_argument("--dte", type=int, default=30)
    p.add_argument("--w-iv", type=float, default=1.0, dest="w_iv")
    p.add_argument("--w-liq", type=float, default=0.5, dest="w_liq")
    p.add_argument("--earnings-penalty", type=float, default=0.40, dest="earnings_penalty")
    args = p.parse_args()

    personal = resolve_personal_dir()
    positional = [t.upper() for t in args.tickers]
    use_wl = args.watchlist or not positional
    wl_tickers = load_watchlist(personal) if use_wl else []
    if positional and use_wl:
        seen, tickers = set(), []
        for t in positional + wl_tickers:
            if t not in seen:
                seen.add(t)
                tickers.append(t)
    elif positional:
        tickers = positional
    else:
        tickers = wl_tickers

    if not tickers:
        sys.exit(
            "No tickers. Pass them as args or populate "
            f"{personal / 'watchlist.toml'} under [watchlist] tickers."
        )

    expirations = fetch_expirations(tickers[0])
    if not expirations:
        sys.exit(f"No expirations for {tickers[0]}")
    expiry, dte = pick_expiry(expirations, args.dte)

    print(f"Scanning {len(tickers)} tickers × 1 expiry (near={expiry})...", file=sys.stderr)

    spots = fetch_prices(tickers)
    earnings = fetch_earnings(tickers)

    chains: dict[str, list[dict] | None] = {}

    def _fetch(t: str) -> tuple[str, list[dict] | None]:
        try:
            return t, fetch_chain(t, expiry)
        except Exception as e:
            print(f"chain fetch failed: {t} {expiry}: {e}", file=sys.stderr)
            return t, None

    with ThreadPoolExecutor(max_workers=4) as ex:
        for t, chain in ex.map(_fetch, tickers):
            chains[t] = chain

    rows = []
    for t in tickers:
        spot = spots.get(t)
        if not spot:
            print(f"price missing for {t}; skipping", file=sys.stderr)
            continue
        chain = chains.get(t)
        m = derive_metrics(chain, spot, dte) if chain else {}

        ed, ddte = earnings.get(t, (None, None))
        earn_in_window = bool(ed and ddte is not None and ddte <= dte)

        score = compute_score(
            m.get("atm_iv"),
            m.get("atm_spread_pct"),
            earn_in_window,
            w_iv=args.w_iv,
            w_liq=args.w_liq,
            earnings_penalty=args.earnings_penalty,
        )
        rows.append(
            {
                "ticker": t,
                "spot": spot,
                "atm_iv": m.get("atm_iv"),
                "skew": m.get("skew"),
                "spread": m.get("atm_spread_pct"),
                "exp_move_pct": m.get("exp_move_pct"),
                "earn_date": ed,
                "earn_in_window": earn_in_window,
                "score": score,
            }
        )

    render(rows, expiry, dte)


if __name__ == "__main__":
    main()
