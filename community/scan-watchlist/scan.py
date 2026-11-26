"""scan-watchlist helper — analyzed options-context scanner.

For each ticker in the watchlist, fetches near + far chains, computes
ATM IV / 25Δ skew / term-structure slope / expected move / liquidity,
appends today's ATM IV to a per-ticker history, derives an IV rank
from that history, applies a strategy decision tree, and ranks the
result by a composite score.

All thresholds (decision-tree cutoffs, score weights, liquidity
benchmarks, history length) are flags — defaults are starting
heuristics, not opinions to inherit silently. See SKILL.md for the
full flag list.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path

SNAPSHOT_NAME_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# Tickers whose options don't sit on the standard monthly Friday cycle, so the
# scan-wide near/far expiry (picked from the first other ticker's list) usually
# isn't listed for them: VIX options expire on Wednesdays. Each gets its own
# pair at the same target DTEs. The same set as thetabook's
# `tickers.FORWARD_SETTLED` today, kept here because this helper talks to
# thetabook only through its CLI.
ODD_EXPIRY_CYCLE: frozenset[str] = frozenset({"VIX", "VIXW"})

UV = os.path.expanduser("~/.local/bin/uv")
SHARE_ASSET_TYPES = {"EQUITY", "ETF", "COLLECTIVE_INVESTMENT", "MUTUAL_FUND"}
TODAY = date.today().isoformat()


@dataclass(frozen=True)
class Tunables:
    """All decision-tree + scoring thresholds, set once from CLI flags."""

    iv_rank_high: float
    iv_rank_low: float
    skew_put_rich: float
    term_slope_strong: float
    min_history_days: int
    w_rank: float
    w_liquidity: float
    earnings_penalty: float
    liquidity_good_oi: int
    liquidity_good_spread: float


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


def resolve_personal_dir(override: str | None) -> Path:
    """personal/ resolution: --personal-dir > snapshot --path > cwd/personal."""
    if override:
        p = Path(override).expanduser()
        if not p.exists():
            raise SystemExit(f"--personal-dir does not exist: {p}")
        return p
    s = cli("snapshot", "--path", check=False).strip()
    if s:
        snap = Path(s)
        if snap.exists():
            return snap.parent.parent
    fallback = Path.cwd() / "personal"
    if not fallback.exists():
        raise SystemExit(
            "Could not resolve personal/. Pass --personal-dir PATH, run "
            "`thetabook refresh` to create a snapshot, or run from a project root."
        )
    return fallback


def load_watchlist(personal: Path) -> tuple[list[str], dict[str, str]]:
    f = personal / "watchlist.toml"
    if not f.is_file():
        return [], {}
    import tomllib

    with f.open("rb") as fp:
        data = tomllib.load(fp)
    return (
        list(data.get("watchlist", {}).get("tickers", [])),
        dict(data.get("notes", {})),
    )


def append_to_watchlist(personal: Path, novel: list[str]) -> list[str]:
    """Append novel tickers; return what was actually added.

    Caveat: this rewrite drops top-level comments in watchlist.toml.
    [notes] keys/values are preserved verbatim via TOML round-trip.
    """
    f = personal / "watchlist.toml"
    data: dict = {}
    if f.exists():
        import tomllib

        with f.open("rb") as fp:
            data = tomllib.load(fp)
    existing = list(data.get("watchlist", {}).get("tickers", []))
    existing_set = set(existing)
    added = [t for t in novel if t not in existing_set]
    if not added:
        return []
    new_tickers = existing + added
    notes = data.get("notes", {})

    out = ["[watchlist]"]
    quoted = ", ".join(f'"{t}"' for t in new_tickers)
    out.append(f"tickers = [{quoted}]")
    out.append("")
    out.append("[notes]")
    for k, v in notes.items():
        v_esc = str(v).replace("\\", "\\\\").replace('"', '\\"')
        out.append(f'{k} = "{v_esc}"')
    out.append("")
    f.write_text("\n".join(out))
    return added


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


def fetch_dividends(tickers: list[str]) -> dict[str, tuple[str | None, int | None]]:
    out = cli("dividends", *tickers, check=False)
    result: dict[str, tuple[str | None, int | None]] = {t: (None, None) for t in tickers}
    for line in out.splitlines():
        parts = line.split()
        if not parts or parts[0] not in tickers:
            continue
        for p in parts:
            if p.startswith("ex-div="):
                ds = p.split("=", 1)[1]
                if not ds or "—" in ds:
                    continue
                try:
                    d = date.fromisoformat(ds)
                    result[parts[0]] = (ds, (d - date.today()).days)
                except ValueError:
                    pass
    return result


# ---------------------------------------------------------------------------
# Metrics, history, holdings
# ---------------------------------------------------------------------------


def derive_metrics(chain: list[dict], spot: float, dte: int) -> dict:
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
    atm_oi = (atm_call.get("open_interest", 0) if atm_call else 0) + (
        atm_put.get("open_interest", 0) if atm_put else 0
    )

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
        "atm_oi": atm_oi,
        "atm_spread_pct": atm_spread_pct,
        "skew": skew,
        "exp_move_pct": exp_move_pct,
    }


def update_history_and_rank(
    personal: Path,
    ticker: str,
    atm_iv: float | None,
    min_history_days: int,
) -> tuple[float | str, int]:
    h_dir = personal / "iv_history"
    h_dir.mkdir(parents=True, exist_ok=True)
    f = h_dir / f"{ticker}.jsonl"
    history = []
    if f.exists():
        for line in f.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    history.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    if atm_iv and (not history or history[-1].get("date") != TODAY):
        with f.open("a") as fp:
            fp.write(json.dumps({"date": TODAY, "atm_iv": atm_iv}) + "\n")
        history.append({"date": TODAY, "atm_iv": atm_iv})
    n = len(history)
    if n < min_history_days or not atm_iv:
        return "INSUFFICIENT", n
    ivs = [h["atm_iv"] for h in history if h.get("atm_iv")]
    if not ivs:
        return "INSUFFICIENT", n
    lo, hi = min(ivs), max(ivs)
    if hi == lo:
        return 0.5, n
    return (atm_iv - lo) / (hi - lo), n


def get_holdings(personal: Path) -> dict[str, dict]:
    """{ticker: {"shares": bool, "options": bool}} from latest snapshot.

    Snapshot files are named ``YYYY-MM-DD.json`` per thetabook convention.
    Cache files (``earnings_cache.json``, ``dividends_cache.json``) live in
    the same directory and get rewritten by the skill's own CLI calls during
    a scan, so picking the freshest by mtime would resolve to the cache —
    which has no ``accounts`` key. Lex-sort on the ISO-dated stems instead.
    """
    snap_dir = personal / "snapshots"
    if not snap_dir.exists():
        return {}
    snap_files = [f for f in snap_dir.glob("*.json") if SNAPSHOT_NAME_RE.fullmatch(f.stem)]
    if not snap_files:
        return {}
    latest = max(snap_files, key=lambda p: p.stem)
    snap = json.loads(latest.read_text())
    h: dict[str, dict] = {}
    for acct in snap.get("accounts", []):
        for pos in acct.get("positions", []):
            inst = pos.get("instrument", {})
            atype = inst.get("assetType")
            sym = inst.get("symbol")
            und = inst.get("underlyingSymbol")
            if atype in SHARE_ASSET_TYPES and sym:
                h.setdefault(sym, {"shares": False, "options": False})["shares"] = True
            elif atype == "OPTION" and und:
                h.setdefault(und, {"shares": False, "options": False})["options"] = True
    return h


def liquidity_score(oi: int, spread_pct: float | None, t: Tunables) -> float:
    oi_score = min(1.0, oi / t.liquidity_good_oi) if t.liquidity_good_oi else 0.0
    spread_score = (
        max(0.0, 1.0 - (spread_pct / t.liquidity_good_spread))
        if spread_pct is not None
        else 0.0
    )
    return (oi_score + spread_score) / 2.0


# ---------------------------------------------------------------------------
# Decision tree + score
# ---------------------------------------------------------------------------


def decide_strategy(
    rank: float | str,
    hist_n: int,
    skew: float | None,
    term_slope: float | None,
    earnings_in_window: bool,
    held_as_shares: bool,
    t: Tunables,
) -> str:
    """Strategy decision tree. Tuned via Tunables (from CLI flags)."""
    if rank == "INSUFFICIENT":
        return f"watch — building IV history ({hist_n}/{t.min_history_days} days)"
    assert isinstance(rank, float)
    if rank >= t.iv_rank_high:
        if earnings_in_window:
            return "earnings play (straddle/strangle over print) — or skip"
        if held_as_shares:
            return "covered call (premium on existing shares)"
        if skew is not None and skew >= t.skew_put_rich:
            return "short put / put spread (premium + put-skew tailwind)"
        if term_slope is not None and term_slope < -t.term_slope_strong:
            return "calendar (sell rich short-term, buy cheaper long-term)"
        return "strangle / iron condor (range-bound, sell premium)"
    if rank <= t.iv_rank_low:
        if held_as_shares:
            return "diagonal / poor-man's covered call (long vol overlay)"
        if term_slope is not None and term_slope > t.term_slope_strong:
            return "calendar (buy cheap front, normal contango)"
        return "long call / long put / straddle (buy cheap vol)"
    if skew is not None and abs(skew) >= t.skew_put_rich * 1.6:
        return "ratio spread / risk reversal (skew trade)"
    return "watch — no clear setup"


def compute_score(
    rank: float | str, liquidity: float, earnings_in_window: bool, t: Tunables
) -> float:
    if rank == "INSUFFICIENT":
        return 0.0
    assert isinstance(rank, float)
    ef = (1.0 - t.earnings_penalty) if earnings_in_window else 1.0
    return (t.w_rank * rank + t.w_liquidity * liquidity) * ef


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def fmt_pct(v: float | None, digits: int = 0) -> str:
    return f"{v * 100:.{digits}f}%" if v is not None else "—"


def fmt_signed_vp(v: float | None) -> str:
    return f"{v * 100:+.1f}vp" if v is not None else "—"


def render(
    rows: list[dict],
    near_expiry: str,
    near_dte: int,
    far_expiry: str,
    far_dte: int,
    notes_map: dict[str, str],
    filter_msg: str | None,
    t: Tunables,
) -> None:
    rows.sort(key=lambda r: r["score"], reverse=True)
    print()
    print(
        f"Watchlist scan — near {near_expiry} ({near_dte} DTE) / "
        f"far {far_expiry} ({far_dte} DTE), {len(rows)} tickers"
    )
    print()
    if filter_msg:
        print(filter_msg)
        print()

    header = (
        f"{'Ticker':<6}  {'Spot':>9}  {'ATM IV':>6}  {'Rank':>6}  "
        f"{'Skew':>7}  {'Term':>7}  {'Liq':>4}  {'Score':>5}  "
        f"{'Earn':>4}  {'ExDiv':>5}  {'ExpMv':>6}  {'Held':>4}  "
        f"Suggested Strategy"
    )
    print(header)
    print("-" * len(header))
    for r in rows:
        rank_s = f"{r['rank']:.2f}" if isinstance(r["rank"], float) else "INSUF"
        held = "—"
        if r["held_as_shares"] and r["held_options"]:
            held = "S+O"
        elif r["held_as_shares"]:
            held = "shrs"
        elif r["held_options"]:
            held = "opts"
        em = f"±{r['exp_move_pct'] * 100:.1f}%" if r["exp_move_pct"] is not None else "—"
        print(
            f"{r['ticker']:<6}  ${r['spot']:>8.2f}  {fmt_pct(r['atm_iv']):>6}  "
            f"{rank_s:>6}  {fmt_signed_vp(r['skew']):>7}  "
            f"{fmt_signed_vp(r['term_slope']):>7}  "
            f"{r['liq']:>4.2f}  {r['score']:>5.2f}  "
            f"{('✓' if r['earn_in_window'] else '—'):>4}  "
            f"{('✓' if r['exdiv_in_window'] else '—'):>5}  "
            f"{em:>6}  {held:>4}  {r['strategy']}"
        )

    print()
    print("Notes:")
    any_note = False
    for r in rows:
        notes = []
        if r.get("expiry_note"):
            notes.append(r["expiry_note"])
        if r["rank"] == "INSUFFICIENT":
            notes.append(f"IV history thin ({r['hist_n']}/{t.min_history_days} days)")
        if r["earn_in_window"]:
            notes.append(
                f"⚠ earnings {r['earn_date']} INSIDE near expiry — "
                "score reduced by earnings_penalty"
            )
        if r["exdiv_in_window"]:
            notes.append(f"⚠ ex-div {r['exdiv_date']} inside near expiry")
        if r["earn_date"] is None:
            notes.append("earnings unknown (yfinance has no calendar)")
        ext = notes_map.get(r["ticker"])
        if ext:
            notes.append(f"watchlist: {ext}")
        if notes:
            any_note = True
            print(f"  {r['ticker']}: " + "; ".join(notes))
    if not any_note:
        print("  (none)")
    print()
    print("Legend: vp = vol points. Skew = 25Δ put IV − 25Δ call IV.")
    print(
        "Term = ATM IV(far) − ATM IV(near).  "
        "Score = (w_rank × rank + w_liquidity × liquidity) × earnings_factor."
    )
    print("Held: shrs = shares only, opts = options only, S+O = both.")


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


def main() -> None:
    p = argparse.ArgumentParser(prog="scan-watchlist")
    p.add_argument("tickers", nargs="*")
    p.add_argument("--watchlist", action="store_true")
    p.add_argument("--personal-dir", dest="personal_dir")
    p.add_argument("--expiry")
    p.add_argument("--dte", type=int, default=30)
    p.add_argument("--term-dte", type=int, default=60, dest="term_dte")

    # Decision-tree thresholds
    p.add_argument("--iv-rank-high", type=float, default=0.60, dest="iv_rank_high")
    p.add_argument("--iv-rank-low", type=float, default=0.30, dest="iv_rank_low")
    p.add_argument("--skew-put-rich", type=float, default=0.05, dest="skew_put_rich")
    p.add_argument("--term-slope-strong", type=float, default=0.02, dest="term_slope_strong")
    p.add_argument("--min-history-days", type=int, default=10, dest="min_history_days")

    # Score weights
    p.add_argument("--w-rank", type=float, default=1.0, dest="w_rank")
    p.add_argument("--w-liquidity", type=float, default=0.5, dest="w_liquidity")
    p.add_argument("--earnings-penalty", type=float, default=0.40, dest="earnings_penalty")

    # Liquidity benchmarks
    p.add_argument("--liquidity-good-oi", type=int, default=500, dest="liquidity_good_oi")
    p.add_argument(
        "--liquidity-good-spread", type=float, default=0.05, dest="liquidity_good_spread"
    )

    # Filters / outputs
    p.add_argument("--min-iv", type=float, dest="min_iv")
    p.add_argument("--strategy")
    p.add_argument("--add-to-watchlist", action="store_true", dest="add_to_watchlist")
    args = p.parse_args()

    t = Tunables(
        iv_rank_high=args.iv_rank_high,
        iv_rank_low=args.iv_rank_low,
        skew_put_rich=args.skew_put_rich,
        term_slope_strong=args.term_slope_strong,
        min_history_days=args.min_history_days,
        w_rank=args.w_rank,
        w_liquidity=args.w_liquidity,
        earnings_penalty=args.earnings_penalty,
        liquidity_good_oi=args.liquidity_good_oi,
        liquidity_good_spread=args.liquidity_good_spread,
    )

    personal = resolve_personal_dir(args.personal_dir)
    positional = [tk.upper() for tk in args.tickers]
    use_wl = args.watchlist or not positional
    wl_tickers, wl_notes = ([], {})
    if use_wl:
        wl_tickers, wl_notes = load_watchlist(personal)

    if positional and use_wl:
        seen, tickers = set(), []
        for tk in positional + wl_tickers:
            if tk not in seen:
                seen.add(tk)
                tickers.append(tk)
    elif positional:
        tickers = positional
    else:
        tickers = wl_tickers

    if not tickers:
        sys.exit(
            "No tickers. Pass them as args or populate "
            f"{personal / 'watchlist.toml'} under [watchlist] tickers."
        )

    # The scan-wide pair comes from a regular-cycle ticker when there is one:
    # with VIX first, every other ticker was sent VIX's Wednesday expiries.
    reference = next((tk for tk in tickers if tk.upper().lstrip("$^") not in ODD_EXPIRY_CYCLE), tickers[0])
    expirations = fetch_expirations(reference)
    if not expirations:
        sys.exit(f"No expirations for {reference}")

    if args.expiry:
        near_expiry = args.expiry
        near_dte = (date.fromisoformat(near_expiry) - date.today()).days
    else:
        near_expiry, near_dte = pick_expiry(expirations, args.dte)
    far_expiry, far_dte = pick_expiry(expirations, args.term_dte)

    print(
        f"Scanning {len(tickers)} tickers × 2 expiries "
        f"(near={near_expiry}, far={far_expiry})...",
        file=sys.stderr,
    )

    spots = fetch_prices(tickers)
    earnings = fetch_earnings(tickers)
    dividends = fetch_dividends(tickers)
    holdings = get_holdings(personal)

    # An odd-cycle ticker resolves its own near/far expiry off its own
    # expiration list, at the DTEs everyone else got.
    ticker_near: dict[str, tuple[str, int]] = {}
    ticker_far: dict[str, tuple[str, int]] = {}
    expiry_note: dict[str, str] = {}
    for tk in tickers:
        if tk.upper().lstrip("$^") in ODD_EXPIRY_CYCLE:
            try:
                own = fetch_expirations(tk)
            except Exception as e:
                print(f"expirations fetch failed: {tk}: {e}", file=sys.stderr)
                own = []
            if own:
                ticker_near[tk] = pick_expiry(own, near_dte)
                ticker_far[tk] = pick_expiry(own, far_dte)
                (tn, tn_dte), (tf, tf_dte) = ticker_near[tk], ticker_far[tk]
                if (tn, tf) != (near_expiry, far_expiry):
                    expiry_note[tk] = (
                        f"odd expiry cycle — own near {tn} ({tn_dte} DTE) / far {tf} ({tf_dte} DTE), "
                        "not the scan-wide pair"
                    )
                continue
        ticker_near[tk] = (near_expiry, near_dte)
        ticker_far[tk] = (far_expiry, far_dte)

    chains: dict[tuple[str, str], list[dict] | None] = {}
    specs = [(tk, ticker_near[tk][0]) for tk in tickers] + [(tk, ticker_far[tk][0]) for tk in tickers]

    def _fetch(spec: tuple[str, str]) -> tuple[tuple[str, str], list[dict] | None]:
        try:
            return spec, fetch_chain(*spec)
        except Exception as e:
            print(f"chain fetch failed: {spec[0]} {spec[1]}: {e}", file=sys.stderr)
            return spec, None

    with ThreadPoolExecutor(max_workers=4) as ex:
        for spec, chain in ex.map(_fetch, specs):
            chains[spec] = chain

    rows = []
    for tk in tickers:
        spot = spots.get(tk)
        if not spot:
            print(f"price missing for {tk}; skipping", file=sys.stderr)
            continue
        tn_expiry, tn_dte = ticker_near[tk]
        tf_expiry, tf_dte = ticker_far[tk]
        near = chains.get((tk, tn_expiry))
        far = chains.get((tk, tf_expiry))
        nm = derive_metrics(near, spot, tn_dte) if near else None
        fm = derive_metrics(far, spot, tf_dte) if far else None

        atm_iv = nm["atm_iv"] if nm else None
        far_iv = fm["atm_iv"] if fm else None
        term_slope = (far_iv - atm_iv) if (atm_iv and far_iv) else None

        rank, hist_n = update_history_and_rank(personal, tk, atm_iv, t.min_history_days)
        liq = liquidity_score(
            nm["atm_oi"] if nm else 0,
            nm["atm_spread_pct"] if nm else None,
            t,
        )

        ed, ddte = earnings.get(tk, (None, None))
        earn_in_window = bool(ed and ddte is not None and ddte <= tn_dte)
        xd, xdte = dividends.get(tk, (None, None))
        exdiv_in_window = bool(xd and xdte is not None and xdte <= tn_dte)

        h = holdings.get(tk, {"shares": False, "options": False})
        strat = decide_strategy(
            rank,
            hist_n,
            nm["skew"] if nm else None,
            term_slope,
            earn_in_window,
            h["shares"],
            t,
        )
        score = compute_score(rank, liq, earn_in_window, t)

        rows.append(
            {
                "ticker": tk,
                "spot": spot,
                "atm_iv": atm_iv,
                "rank": rank,
                "hist_n": hist_n,
                "skew": nm["skew"] if nm else None,
                "term_slope": term_slope,
                "liq": liq,
                "score": score,
                "earn_date": ed,
                "earn_in_window": earn_in_window,
                "exdiv_date": xd,
                "exdiv_in_window": exdiv_in_window,
                "exp_move_pct": nm["exp_move_pct"] if nm else None,
                "held_as_shares": h["shares"],
                "held_options": h["options"],
                "strategy": strat,
                "expiry_note": expiry_note.get(tk),
            }
        )

    filter_msg = None
    total_before = len(rows)
    if args.min_iv is not None:
        thr = args.min_iv / 100.0
        rows = [r for r in rows if r["atm_iv"] and r["atm_iv"] >= thr]
    if args.strategy:
        sub = args.strategy.lower()
        rows = [r for r in rows if sub in r["strategy"].lower()]
        filter_msg = (
            f'Filtered to strategy substring: "{args.strategy}" — '
            f"{len(rows)} of {total_before} rows shown."
        )

    render(rows, near_expiry, near_dte, far_expiry, far_dte, wl_notes, filter_msg, t)

    if args.add_to_watchlist and positional:
        added = append_to_watchlist(personal, positional)
        if added:
            print(f"\nAdded to watchlist: {', '.join(added)}")
            print(f"  -> {personal / 'watchlist.toml'}")
        else:
            print("\nWatchlist already contains all positional tickers — no changes.")


if __name__ == "__main__":
    main()
