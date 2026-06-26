"""
build_dxy_proxy.py  --  cheap stand-in for the US Dollar Index.

DXY is ~inversely correlated with EURUSD (EUR is 57.6% of the index), so a
quick proxy for "the dollar" is simply 1 / EURUSD. This is good enough for an
SMT divergence test against gold BEFORE bothering to assemble the full 6-pair
synthetic index.

IMPORTANT on OHLC inversion: when you invert a price series, highs and lows
SWAP. The highest the dollar got in a bar corresponds to the LOWEST EURUSD got,
so:
    proxy_high = 1 / eur_low
    proxy_low  = 1 / eur_high
Getting this wrong silently corrupts every sweep test, so it is done explicitly.

We scale by 100 only so the numbers look index-like; SMT only reads the
high/low STRUCTURE, so the scale is cosmetic.

Usage:
    python build_dxy_proxy.py eurusd_1m.parquet dxy_proxy_1m.parquet
"""

from __future__ import annotations
import sys
import pandas as pd


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "eurusd_1m.parquet"
    out = sys.argv[2] if len(sys.argv) > 2 else "dxy_proxy_1m.parquet"

    eur = pd.read_parquet(src) if src.endswith(".parquet") \
        else pd.read_csv(src, parse_dates=[0], index_col=0)
    eur.index = pd.to_datetime(eur.index, utc=True)
    eur = eur[["open", "high", "low", "close"]].astype(float)
    eur = eur[(eur > 0).all(axis=1)]  # guard against zero/garbage rows

    scale = 100.0
    proxy = pd.DataFrame({
        "open":  scale / eur["open"],
        "high":  scale / eur["low"],    # SWAP: dollar high <-> eur low
        "low":   scale / eur["high"],   # SWAP: dollar low  <-> eur high
        "close": scale / eur["close"],
    }, index=eur.index)

    # sanity: high must be >= low after inversion
    bad = (proxy["high"] < proxy["low"]).sum()
    assert bad == 0, f"{bad} rows have high<low after inversion -- check input"

    proxy.to_parquet(out)
    print(f"DXY proxy (1/EURUSD x{scale:.0f}): {len(proxy):,} rows -> {out}")
    print(f"  range {proxy.index[0]:%Y-%m-%d} -> {proxy.index[-1]:%Y-%m-%d}")
    print(f"  sample close: {proxy['close'].iloc[0]:.4f} "
          f"(EURUSD {eur['close'].iloc[0]:.5f})")


if __name__ == "__main__":
    main()
