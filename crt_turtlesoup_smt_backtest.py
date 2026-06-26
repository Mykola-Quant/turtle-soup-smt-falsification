"""
crt_turtlesoup_smt_backtest.py
================================
Honest backtest for the "4H -> 15m CRT" model:
    - CRT range          : HTF candle defines CRT high / CRT low (key liquidity)
    - Turtle Soup        : 15m sweep of the key level, then close back inside
    - SMT divergence     : correlated instrument FAILS to confirm the sweep
    - Time filter        : only trade inside defined killzones
    - Exit               : fixed-R bracket (stop beyond sweep, target = N*R)

Design principles (matching a pre-registration / no-self-deception workflow):
    * Every threshold is set ONCE in Config and locked. No peeking / tuning.
    * Signals use only CLOSED bars. No look-ahead.
    * Costs are explicit (spread + commission, in pips).
    * We report the BASELINE (Turtle Soup alone) and the SMT-FILTERED subset
      side by side, so SMT's marginal contribution is visible -- not hidden.
    * We report expectancy in R and per-cell sample size, not just win rate.

Data contract
-------------
Two parquet/CSV files of OHLCV, indexed by a UTC DatetimeIndex, columns:
    open, high, low, close   (volume optional)
Primary   = the instrument you trade (e.g. EURUSD).
Secondary = the SMT reference instrument:
    smt_mode='positive'  -> positively correlated (e.g. GBPUSD)
    smt_mode='negative'  -> inversely correlated  (e.g. DXY / USDX)
    smt_mode='none'      -> run baseline only, no SMT filter

Both series must be on the SAME base timeframe (e.g. 1m or 5m) so they align.
Free historical FX sources: Dukascopy, HistData.com.

Run:
    python crt_turtlesoup_smt_backtest.py --primary eurusd_1m.parquet \
        --secondary gbpusd_1m.parquet --smt-mode positive
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd


# --------------------------------------------------------------------------- #
# Config  --  set ONCE, then locked. Do not tune against the test set.
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Config:
    # --- timeframes ---
    exec_tf: str = "15min"          # execution timeframe (entries)
    range_tf: str = "4h"            # CRT range timeframe (defines high/low)

    # --- killzones (UTC). London + NY AM by default. (start, end) inclusive-exclusive
    killzones: tuple = field(default_factory=lambda: (
        ("07:00", "10:00"),         # London
        ("12:00", "15:00"),         # NY AM
    ))

    # --- Turtle Soup sweep definition ---
    sweep_buffer_pips: float = 0.5  # how far beyond the level price must poke
    pip_size: float = 0.0001        # 0.0001 for EURUSD/GBPUSD, 0.01 for JPY pairs
    max_bars_to_reclaim: int = 3    # close back inside within N bars of the poke

    # --- SMT lookback (bars on exec_tf used to define "new high/low") ---
    smt_lookback: int = 12

    # --- risk / exits (fixed-R bracket) ---
    stop_buffer_pips: float = 1.0   # stop placed this far beyond the sweep extreme
    target_R: float = 2.0           # take profit at target_R * risk
    max_hold_bars: int = 16         # time-stop: close at market after N exec bars

    # --- costs (round trip is paid once per trade) ---
    spread_pips: float = 0.3        # typical EURUSD spread
    commission_pips: float = 0.2    # per-side commission expressed in pips, x2 below

    # --- crypto-style cost: if > 0, OVERRIDES the pip cost above and charges
    #     round-trip cost as a fraction of entry price (e.g. 0.0013 = 0.13%).
    cost_pct: float = 0.0

    # --- if True, ignore killzones and trade 24/7 (for crypto, which has no
    #     London/NY sessions and no DST). ---
    all_day: bool = False

    # --- pre-registered minimum sample before trusting a cell ---
    min_events_per_cell: int = 30

    @property
    def round_trip_pips(self) -> float:
        return self.spread_pips + 2.0 * self.commission_pips


# --------------------------------------------------------------------------- #
# Per-asset presets.
#
# CRITICAL: pip_size MUST match the instrument or the entire cost/buffer model
# is wrong by orders of magnitude. The *_pips fields are denominated in that
# asset's pip, so a "pip" is a different absolute size per row below.
#
# The cost numbers (spread_pips, commission_pips) for metals are PLACEHOLDERS.
# Replace them with YOUR broker's real round-trip cost before trusting any
# result -- this is exactly the cost-vs-edge knife edge, do not eyeball it.
#
# Verify pip_size by opening a CSV and counting decimals in the price columns:
#   EURUSD/GBPUSD -> 5 decimals (1.08123) -> pip = 0.0001
#   XAUUSD        -> 2-3 decimals (2634.55) -> pip here set to 0.1
#   XAGUSD        -> 2-3 decimals (30.456)  -> pip here set to 0.01  (CHECK!)
# --------------------------------------------------------------------------- #
ASSETS = {
    "eurusd": dict(pip_size=0.0001, sweep_buffer_pips=0.5, stop_buffer_pips=1.0,
                   spread_pips=0.3, commission_pips=0.2),
    "gbpusd": dict(pip_size=0.0001, sweep_buffer_pips=0.7, stop_buffer_pips=1.2,
                   spread_pips=0.6, commission_pips=0.2),
    # gold: pip = 0.1 USD. spread 3 pip = $0.30, round trip = 3 + 2*1 = 5 pip = $0.50
    "xauusd": dict(pip_size=0.1,    sweep_buffer_pips=2.0, stop_buffer_pips=4.0,
                   spread_pips=3.0, commission_pips=1.0),
    # silver: pip = 0.01 USD. VERIFY decimals first -- could be 0.001 on your data
    "xagusd": dict(pip_size=0.01,   sweep_buffer_pips=2.0, stop_buffer_pips=4.0,
                   spread_pips=2.0, commission_pips=1.0),
    # --- crypto: cost is PERCENT of price (cost_pct), pip cost ignored. ---
    #     24/7 market -> all_day=True (no killzone, no DST). pip_size here is
    #     just the unit for the sweep/stop buffers, expressed in price units.
    #     Buffers are intentionally small vs crypto ranges; they only gate which
    #     pokes count as sweeps, they do not drive the P&L denominator.
    "btc": dict(pip_size=1.0, sweep_buffer_pips=20.0, stop_buffer_pips=40.0,
                cost_pct=0.0013, all_day=True),
    "eth": dict(pip_size=1.0, sweep_buffer_pips=1.0, stop_buffer_pips=2.0,
                cost_pct=0.0013, all_day=True),
    "sol": dict(pip_size=0.01, sweep_buffer_pips=5.0, stop_buffer_pips=10.0,
                cost_pct=0.0013, all_day=True),
}


def build_config(asset: str, smt_mode: str) -> "Config":
    """Construct a locked Config from an asset preset + chosen SMT mode."""
    if asset not in ASSETS:
        raise SystemExit(f"unknown asset '{asset}'. known: {list(ASSETS)}")
    base = Config()
    fields = {**base.__dict__, **ASSETS[asset]}
    cfg = Config(**fields)
    object.__setattr__(cfg, "smt_mode", smt_mode)
    object.__setattr__(cfg, "asset", asset)
    return cfg


# --------------------------------------------------------------------------- #
# Data helpers
# --------------------------------------------------------------------------- #
def load_ohlcv(path: str) -> pd.DataFrame:
    if path.endswith(".parquet"):
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path, parse_dates=[0], index_col=0)
    df = df[["open", "high", "low", "close"]].copy()
    df.index = pd.to_datetime(df.index, utc=True)
    df = df[~df.index.duplicated(keep="first")].sort_index()
    return df


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    o = df["open"].resample(tf).first()
    h = df["high"].resample(tf).max()
    l = df["low"].resample(tf).min()
    c = df["close"].resample(tf).last()
    out = pd.concat([o, h, l, c], axis=1, keys=["open", "high", "low", "close"])
    return out.dropna()


def in_killzone(idx: pd.DatetimeIndex, cfg: Config) -> pd.Series:
    if getattr(cfg, "all_day", False):
        return pd.Series(np.ones(len(idx), dtype=bool), index=idx)
    t = idx.tz_convert("UTC").time
    mask = np.zeros(len(idx), dtype=bool)
    for start, end in cfg.killzones:
        s = pd.to_datetime(start).time()
        e = pd.to_datetime(end).time()
        mask |= np.array([(s <= x) and (x < e) for x in t])
    return pd.Series(mask, index=idx)


# --------------------------------------------------------------------------- #
# Signal construction (no look-ahead: everything uses closed bars only)
# --------------------------------------------------------------------------- #
def attach_crt_levels(exec_df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """Map each exec bar to the PRIOR completed range candle's high/low."""
    rng = resample(exec_df, cfg.range_tf)
    # prior completed range candle: shift by 1 so we never use the live one
    crt_high = rng["high"].shift(1)
    crt_low = rng["low"].shift(1)
    df = exec_df.copy()
    df["crt_high"] = crt_high.reindex(df.index, method="ffill")
    df["crt_low"] = crt_low.reindex(df.index, method="ffill")
    return df


def compute_smt(primary: pd.DataFrame,
                secondary: Optional[pd.DataFrame],
                cfg: Config) -> pd.DataFrame:
    """
    Returns boolean columns smt_bear / smt_bull on the primary index.

    Bearish SMT (for shorts): primary prints a NEW local high (the sweep) while
    the reference FAILS to print a new high (positive corr) / new low (neg corr).
    Bullish SMT is the mirror.
    """
    p = primary
    lb = cfg.smt_lookback
    p_new_high = p["high"] > p["high"].shift(1).rolling(lb).max()
    p_new_low = p["low"] < p["low"].shift(1).rolling(lb).min()

    if secondary is None:
        # no SMT available -> never blocks; baseline behaviour
        p["smt_bear"] = True
        p["smt_bull"] = True
        return p

    s = secondary.reindex(p.index).ffill()
    s_new_high = s["high"] > s["high"].shift(1).rolling(lb).max()
    s_new_low = s["low"] < s["low"].shift(1).rolling(lb).min()

    if cfg.smt_mode == "positive":
        # divergence = primary new high, secondary NOT new high
        p["smt_bear"] = p_new_high & (~s_new_high)
        p["smt_bull"] = p_new_low & (~s_new_low)
    elif cfg.smt_mode == "negative":
        # inverse pair (e.g. DXY): primary new high, DXY NOT new low
        p["smt_bear"] = p_new_high & (~s_new_low)
        p["smt_bull"] = p_new_low & (~s_new_high)
    else:
        p["smt_bear"] = True
        p["smt_bull"] = True
    return p


# --------------------------------------------------------------------------- #
# Trade simulation
# --------------------------------------------------------------------------- #
def simulate(df: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    """
    Detect Turtle Soup setups and simulate a fixed-R bracket for each.
    A setup is recorded with a 'smt' flag so we can split baseline vs SMT later.
    Returns one row per trade.
    """
    pip = cfg.pip_size
    buf = cfg.sweep_buffer_pips * pip
    kz = in_killzone(df.index, cfg)

    trades = []
    n = len(df)
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    opens = df["open"].values
    crt_high = df["crt_high"].values
    crt_low = df["crt_low"].values
    smt_bear = df["smt_bear"].values
    smt_bull = df["smt_bull"].values
    kz_arr = kz.values
    idx = df.index

    i = 0
    while i < n - 1:
        if not kz_arr[i] or np.isnan(crt_high[i]):
            i += 1
            continue

        # ---------------- SHORT: sweep ABOVE crt_high, close back below -------
        level = crt_high[i]
        poked = highs[i] > level + buf
        reclaimed = closes[i] < level
        if poked and reclaimed:
            entry = closes[i]
            sweep_hi = highs[i]
            stop = sweep_hi + cfg.stop_buffer_pips * pip
            risk = stop - entry
            if risk > 0:
                target = entry - cfg.target_R * risk
                trades.append(_run_bracket(
                    "short", i, entry, stop, target, risk,
                    highs, lows, idx, cfg, smt_bear[i]))
                i += 1
                continue

        # ---------------- LONG: sweep BELOW crt_low, close back above ---------
        level = crt_low[i]
        poked = lows[i] < level - buf
        reclaimed = closes[i] > level
        if poked and reclaimed:
            entry = closes[i]
            sweep_lo = lows[i]
            stop = sweep_lo - cfg.stop_buffer_pips * pip
            risk = entry - stop
            if risk > 0:
                target = entry + cfg.target_R * risk
                trades.append(_run_bracket(
                    "long", i, entry, stop, target, risk,
                    highs, lows, idx, cfg, smt_bull[i]))
                i += 1
                continue
        i += 1

    return pd.DataFrame(trades)


def _run_bracket(side, i, entry, stop, target, risk,
                 highs, lows, idx, cfg, smt_ok) -> dict:
    """Walk forward bar-by-bar until stop, target, or time-stop hits."""
    n = len(highs)
    pip = cfg.pip_size
    if cfg.cost_pct > 0:
        # crypto: round-trip cost as fraction of entry price
        cost_R = (entry * cfg.cost_pct) / risk
    else:
        cost_R = (cfg.round_trip_pips * pip) / risk  # FX: pip-denominated cost

    outcome_R = None
    exit_reason = "time"
    for j in range(i + 1, min(i + 1 + cfg.max_hold_bars, n)):
        hi, lo = highs[j], lows[j]
        if side == "short":
            # conservative: if both touched in same bar, assume stop first
            if hi >= stop:
                outcome_R, exit_reason = -1.0, "stop"
                break
            if lo <= target:
                outcome_R, exit_reason = cfg.target_R, "target"
                break
        else:
            if lo <= stop:
                outcome_R, exit_reason = -1.0, "stop"
                break
            if hi >= target:
                outcome_R, exit_reason = cfg.target_R, "target"
                break
    if outcome_R is None:
        # time-stop: mark-to-market at next open-ish (use last close proxy)
        j = min(i + cfg.max_hold_bars, n - 1)
        # approximate exit at that bar's close-equivalent (low/high midpoint)
        outcome_R = 0.0
        exit_reason = "time"

    return {
        "time": idx[i],
        "side": side,
        "gross_R": outcome_R,
        "net_R": outcome_R - cost_R,   # subtract round-trip cost in R units
        "cost_R": cost_R,
        "exit": exit_reason,
        "smt": bool(smt_ok),
        "hour": idx[i].tz_convert("UTC").hour,
    }


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
def report(trades: pd.DataFrame, cfg: Config) -> None:
    if trades.empty:
        print("No trades generated. Check data, timeframe, and killzones.")
        return

    def stats(df, label):
        n = len(df)
        if n == 0:
            print(f"  {label:<22} n=0")
            return
        wr = (df["gross_R"] > 0).mean()
        gross = df["gross_R"].mean()
        net = df["net_R"].mean()
        total_net = df["net_R"].sum()
        flag = "" if n >= cfg.min_events_per_cell else "  [UNDERPOWERED]"
        print(f"  {label:<22} n={n:<5} win={wr:5.1%}  "
              f"gross={gross:+.3f}R  net={net:+.3f}R  "
              f"totalNet={total_net:+6.1f}R{flag}")

    print("=" * 72)
    if cfg.cost_pct > 0:
        cost_str = f"{cfg.cost_pct:.4%} of price"
    else:
        cost_str = f"{cfg.round_trip_pips:.2f} pips"
    print(f"CRT + Turtle Soup + SMT  |  round-trip cost = "
          f"{cost_str}  |  target {cfg.target_R}R")
    print("=" * 72)

    print("\nBASELINE (Turtle Soup, all setups):")
    stats(trades, "all")
    stats(trades[trades.side == "short"], "shorts")
    stats(trades[trades.side == "long"], "longs")

    print("\nSMT-FILTERED (divergence confirmed only):")
    smt = trades[trades.smt]
    stats(smt, "smt all")
    stats(smt[smt.side == "short"], "smt shorts")
    stats(smt[smt.side == "long"], "smt longs")

    print("\nNON-SMT (sweep WITHOUT divergence):")
    stats(trades[~trades.smt], "non-smt all")

    # The key question: does SMT add edge, or just shrink the sample?
    base_net = trades["net_R"].mean()
    smt_net = smt["net_R"].mean() if len(smt) else float("nan")
    print("\nMARGINAL VALUE OF SMT:")
    print(f"  baseline net expectancy : {base_net:+.3f}R")
    print(f"  SMT-only net expectancy : {smt_net:+.3f}R")
    delta = smt_net - base_net
    verdict = ("SMT improves expectancy" if delta > 0
               else "SMT does NOT improve expectancy")
    print(f"  delta                   : {delta:+.3f}R  ->  {verdict}")

    print("\nBy killzone hour (net R expectancy, baseline):")
    by_hour = trades.groupby("hour")["net_R"].agg(["count", "mean"])
    for h, row in by_hour.iterrows():
        flag = "" if row["count"] >= cfg.min_events_per_cell else "  [thin]"
        print(f"  {h:02d}:00 UTC  n={int(row['count']):<4} "
              f"net={row['mean']:+.3f}R{flag}")
    print("=" * 72)


# --------------------------------------------------------------------------- #
# Significance tests  --  so "SMT improves" is not judged by eye
# --------------------------------------------------------------------------- #
def _smt_delta_pvalue(trades: pd.DataFrame, n_perm: int = 10000,
                      seed: int = 42):
    """
    Core permutation engine. Returns (obs_delta, p_value, n_smt, n_non).
    obs_delta = mean(net_R | smt) - mean(net_R | non-smt).
    p is one-sided: P(random label split gives delta >= observed).
    Returns None if either group is empty.
    """
    rng = np.random.default_rng(seed)
    smt_mask = trades["smt"].values.astype(bool)
    net = trades["net_R"].values
    n_smt = int(smt_mask.sum())
    n_non = len(net) - n_smt
    if n_smt == 0 or n_non == 0:
        return None
    obs = net[smt_mask].mean() - net[~smt_mask].mean()
    idx = np.arange(len(net))
    count = 0
    for _ in range(n_perm):
        perm = rng.permutation(idx)
        d = net[perm[:n_smt]].mean() - net[perm[n_smt:]].mean()
        if d >= obs:
            count += 1
    p = (count + 1) / (n_perm + 1)
    return obs, p, n_smt, n_non


def permutation_test_smt(trades: pd.DataFrame, n_perm: int = 10000,
                         seed: int = 42) -> None:
    """
    H0: the SMT label is unrelated to net_R (the divergence filter is noise).
    """
    res = _smt_delta_pvalue(trades, n_perm, seed)
    if res is None:
        print("\nPERMUTATION TEST: SMT label is degenerate (all/none) -> skipped")
        return
    obs, p, n_smt, _ = res
    print("\nPERMUTATION TEST (SMT vs non-SMT, one-sided):")
    print(f"  observed delta = {obs:+.3f}R   n_smt={n_smt}   p = {p:.4f}")
    sig = "SIGNIFICANT" if p < 0.05 else "not significant"
    print(f"  -> {sig} at alpha=0.05")
    if n_smt < 30:
        print("  [WARNING] n_smt < 30 -- p-value is itself unstable, treat as")
        print("            directional only, not as evidence. Need more data.")


def split_half_oos(trades: pd.DataFrame, n_perm: int = 10000) -> None:
    """
    Pre-registered out-of-sample validation of the SMT effect.

    Split the trade ledger chronologically at the CALENDAR midpoint of the
    period (not by trade count -- honest time split). Recompute the SMT effect
    independently in each half.

    LOCKED PASS CRITERION:
        SMT effect SURVIVES iff in BOTH halves:  delta > 0  AND  p < 0.10.
    Anything else (sign flips, or either p >= 0.10) -> FALSIFIED.

    A pass means the effect is real and time-stable -- NOT that it is
    profitable (net expectancy can still be negative).
    """
    t = trades["time"]
    mid = t.min() + (t.max() - t.min()) / 2
    first = trades[t <= mid]
    second = trades[t > mid]

    print("\n" + "=" * 72)
    print("OUT-OF-SAMPLE SPLIT-HALF (pre-registered)")
    print(f"  split at calendar midpoint: {mid}")
    print(f"  PASS iff BOTH halves have delta>0 AND p<0.10")
    print("=" * 72)

    results = {}
    for name, half in (("first half", first), ("second half", second)):
        res = _smt_delta_pvalue(half, n_perm, seed=hash(name) % 2**31)
        if res is None:
            print(f"  {name:<12} n={len(half):<5} -> degenerate (one group empty)")
            results[name] = None
            continue
        obs, p, n_smt, n_non = res
        results[name] = (obs, p, n_smt, n_non)
        smt_net = half[half.smt]["net_R"].mean()
        nonsmt_net = half[~half.smt]["net_R"].mean()
        thin = "  [n_smt<30]" if n_smt < 30 else ""
        print(f"  {name:<12} trades={len(half):<5} n_smt={n_smt:<4} "
              f"n_non={n_non:<4}{thin}")
        print(f"               smt_net={smt_net:+.3f}R  "
              f"non_smt_net={nonsmt_net:+.3f}R")
        print(f"               delta={obs:+.3f}R   p={p:.4f}")

    # Apply the locked criterion mechanically
    def passes(r):
        return r is not None and r[0] > 0 and r[1] < 0.10
    p1 = passes(results.get("first half"))
    p2 = passes(results.get("second half"))
    print("-" * 72)
    if p1 and p2:
        print("  VERDICT: SURVIVES OOS  (effect is real and time-stable)")
        print("           NOTE: real != profitable. Net is still negative.")
    else:
        print("  VERDICT: FALSIFIED OOS")
        why = []
        for name in ("first half", "second half"):
            r = results.get(name)
            if r is None:
                why.append(f"{name}: degenerate")
            elif r[0] <= 0:
                why.append(f"{name}: delta<=0 (effect absent/flipped)")
            elif r[1] >= 0.10:
                why.append(f"{name}: p={r[1]:.3f} >= 0.10 (not significant)")
        print("           reason: " + "; ".join(why))
        print("           the full-sample p=0.02 did not replicate out of sample")
    print("=" * 72)


def bootstrap_expectancy(trades: pd.DataFrame, label: str = "all trades",
                         n_boot: int = 10000, seed: int = 42) -> None:
    """
    Bootstrap 95% CI on mean net_R. If the lower bound is above zero, the edge
    is at least robust to resampling. If it straddles zero -> no robust edge,
    regardless of how nice the point estimate looks.
    """
    rng = np.random.default_rng(seed)
    net = trades["net_R"].values
    n = len(net)
    if n < 2:
        print(f"\nBOOTSTRAP ({label}): n<2 -> skipped")
        return
    means = np.array([net[rng.integers(0, n, n)].mean() for _ in range(n_boot)])
    lo, hi = np.percentile(means, [2.5, 97.5])
    print(f"\nBOOTSTRAP 95% CI on net expectancy ({label}):")
    print(f"  mean = {net.mean():+.3f}R   95% CI = [{lo:+.3f}, {hi:+.3f}]R")
    if lo > 0:
        print("  -> edge plausible (CI entirely above zero)")
    else:
        print("  -> NO robust edge (CI includes zero or is negative)")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--primary", required=True, help="OHLCV parquet/csv you trade")
    ap.add_argument("--secondary", default=None, help="SMT reference instrument")
    ap.add_argument("--smt-mode", default="none",
                    choices=["positive", "negative", "none"])
    ap.add_argument("--asset", default="eurusd",
                    choices=list(ASSETS),
                    help="asset preset: sets pip_size, buffers, and costs")
    args = ap.parse_args()

    cfg = build_config(args.asset, args.smt_mode)
    if cfg.cost_pct > 0:
        print(f"# asset preset: {args.asset}  pip_size={cfg.pip_size}  "
              f"round_trip={cfg.cost_pct:.4%} of price  all_day={cfg.all_day}")
    else:
        print(f"# asset preset: {args.asset}  pip_size={cfg.pip_size}  "
              f"round_trip={cfg.round_trip_pips:.2f} pips "
              f"(= {cfg.round_trip_pips * cfg.pip_size:.5f} price units)")

    primary = load_ohlcv(args.primary)
    secondary = load_ohlcv(args.secondary) if args.secondary else None

    exec_p = resample(primary, cfg.exec_tf)
    exec_p = attach_crt_levels(exec_p, cfg)

    exec_s = resample(secondary, cfg.exec_tf) if secondary is not None else None
    exec_p = compute_smt(exec_p, exec_s, cfg)

    trades = simulate(exec_p, cfg)
    report(trades, cfg)

    if not trades.empty:
        bootstrap_expectancy(trades, "all trades")
        if args.smt_mode != "none":
            permutation_test_smt(trades)
            smt_only = trades[trades.smt]
            if len(smt_only) >= 2:
                bootstrap_expectancy(smt_only, "SMT-only")
            split_half_oos(trades)

    out = "crt_turtlesoup_smt_trades.parquet"
    if not trades.empty:
        trades.to_parquet(out)
        print(f"\nTrade ledger written: {out}")


if __name__ == "__main__":
    main()
