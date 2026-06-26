"""
build_synthetic_dxy.py  --  full ICE US Dollar Index from 6 FX pairs.

Official ICE formula (geometric, fixed 1973 weights):
    DXY = 50.14348112
          * EURUSD^-0.576
          * USDJPY^+0.136
          * GBPUSD^-0.119
          * USDCAD^+0.091
          * USDSEK^+0.042
          * USDCHF^+0.036

EURUSD and GBPUSD carry NEGATIVE exponents (they are quoted as USD-per-FX, so
a stronger dollar pushes them down). The four USDXXX pairs carry POSITIVE
exponents.

OHLC handling (the subtle part):
  open  uses each pair's open; close uses each pair's close -- straightforward.
  For the index HIGH/LOW we cannot just take max/min of all six pairs, because
  of the sign flip. The index is highest when the negatively-weighted pairs
  (EUR, GBP) are at their LOW and the positively-weighted pairs are at their
  HIGH, and vice-versa. So we build two "extreme" index values per bar:
      idx_a = formula with EUR=low,GBP=low, others=high
      idx_b = formula with EUR=high,GBP=high, others=low
  then high=max(idx_a,idx_b), low=min(...). This is an upper/lower bound on the
  intrabar index range -- correct direction, mildly conservative on width.

Usage:
    python build_synthetic_dxy.py
        (expects eurusd_1m.parquet gbpusd_1m.parquet usdjpy_1m.parquet
         usdcad_1m.parquet usdsek_1m.parquet usdchf_1m.parquet in cwd)
    python build_synthetic_dxy.py --out dxy_synth_1m.parquet
"""

from __future__ import annotations
import argparse
import pandas as pd

CONST = 50.14348112
# (filename-stem, exponent)
COMPONENTS = {
    "eurusd": -0.576,
    "usdjpy": +0.136,
    "gbpusd": -0.119,
    "usdcad": +0.091,
    "usdsek": +0.042,
    "usdchf": +0.036,
}


def load(stem: str) -> pd.DataFrame:
    path = f"{stem}_1m.parquet"
    df = pd.read_parquet(path)
    df.index = pd.to_datetime(df.index, utc=True)
    return df[["open", "high", "low", "close"]].astype(float)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dxy_synth_1m.parquet")
    args = ap.parse_args()

    data = {}
    for stem in COMPONENTS:
        try:
            data[stem] = load(stem)
        except FileNotFoundError:
            raise SystemExit(f"missing {stem}_1m.parquet -- convert it first "
                             f"(python convert_histdata.py {stem.upper()} "
                             f"{stem}_1m.parquet)")

    # align all six on the common timestamp intersection
    idx = None
    for df in data.values():
        idx = df.index if idx is None else idx.intersection(df.index)
    idx = idx.sort_values()
    print(f"common 1m timestamps across 6 pairs: {len(idx):,}")
    data = {k: v.reindex(idx) for k, v in data.items()}

    def index_from(field_map) -> pd.Series:
        """field_map: stem -> column name to use for that pair."""
        out = pd.Series(CONST, index=idx)
        for stem, exp in COMPONENTS.items():
            out = out * data[stem][field_map[stem]] ** exp
        return out

    # open / close straightforward
    dxy_open = index_from({s: "open" for s in COMPONENTS})
    dxy_close = index_from({s: "close" for s in COMPONENTS})

    # high/low: negatively-weighted pairs (eur,gbp) flip
    neg = {s for s, e in COMPONENTS.items() if e < 0}
    hi_map = {s: ("low" if s in neg else "high") for s in COMPONENTS}
    lo_map = {s: ("high" if s in neg else "low") for s in COMPONENTS}
    idx_a = index_from(hi_map)   # index pushed UP
    idx_b = index_from(lo_map)   # index pushed DOWN
    dxy_high = pd.concat([idx_a, idx_b], axis=1).max(axis=1)
    dxy_low = pd.concat([idx_a, idx_b], axis=1).min(axis=1)

    dxy = pd.DataFrame({"open": dxy_open, "high": dxy_high,
                        "low": dxy_low, "close": dxy_close}, index=idx).dropna()

    bad = (dxy["high"] < dxy["low"]).sum()
    assert bad == 0, f"{bad} rows high<low -- component orientation bug"

    dxy.to_parquet(args.out)
    print(f"synthetic DXY: {len(dxy):,} rows  "
          f"{dxy.index[0]:%Y-%m-%d} -> {dxy.index[-1]:%Y-%m-%d}  -> {args.out}")
    print(f"  sample close: {dxy['close'].iloc[0]:.3f}  "
          f"(real DXY usually 90-110, so this should look right)")


if __name__ == "__main__":
    main()
